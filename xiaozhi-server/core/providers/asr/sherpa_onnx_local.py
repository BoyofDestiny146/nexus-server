import glob
import time
import wave
import os
import sys
import io
from config.logger import setup_logging
from typing import Optional, Tuple, List
from core.providers.asr.dto.dto import InterfaceType
from core.providers.asr.base import ASRProviderBase

import numpy as np
import sherpa_onnx

from modelscope.hub.file_download import model_file_download

TAG = __name__
logger = setup_logging()


def _first_existing(model_dir: str, *candidates: str) -> Optional[str]:
    """Return the first candidate filename (relative to model_dir) that exists.

    Each candidate may be a literal name or a glob pattern. Used to locate the
    encoder/decoder/tokens files of a sherpa-onnx Whisper model export, whose
    names are prefixed by the model size (e.g. ``base.en-encoder.int8.onnx``).
    """
    for name in candidates:
        path = os.path.join(model_dir, name)
        if os.path.isfile(path):
            return path
        matches = sorted(glob.glob(path))
        if matches:
            return matches[0]
    return None


def _detect_whisper_model(model_dir: str) -> Optional[dict]:
    """careconnect: detect a sherpa-onnx **Whisper** model export in model_dir.

    SenseVoiceSmall cannot reliably transcribe English (it mis-detects English
    speech as CJK when ``language=auto``, and returns empty with
    ``language=en``), which made the physical SenseCAP Watcher reply in Chinese
    and then get blanked by the CJK-strip guard. We switch the local ASR to an
    English Whisper model. A Whisper export ships ``*-encoder*.onnx``,
    ``*-decoder*.onnx`` and ``*-tokens.txt`` (the size is the filename prefix,
    e.g. ``base.en-``). Prefer the int8-quantized variants for the Orin.

    Returns ``{"encoder", "decoder", "tokens"}`` if a Whisper export is found,
    else ``None`` (caller falls back to the SenseVoice path).
    """
    encoder = _first_existing(
        model_dir, "*-encoder.int8.onnx", "*-encoder.onnx", "encoder.int8.onnx", "encoder.onnx"
    )
    decoder = _first_existing(
        model_dir, "*-decoder.int8.onnx", "*-decoder.onnx", "decoder.int8.onnx", "decoder.onnx"
    )
    tokens = _first_existing(model_dir, "*-tokens.txt", "tokens.txt")
    if encoder and decoder and tokens:
        return {"encoder": encoder, "decoder": decoder, "tokens": tokens}
    return None


# 捕获标准输出
class CaptureOutput:
    def __enter__(self):
        self._output = io.StringIO()
        self._original_stdout = sys.stdout
        sys.stdout = self._output

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stdout = self._original_stdout
        self.output = self._output.getvalue()
        self._output.close()

        # 将捕获到的内容通过 logger 输出
        if self.output:
            logger.bind(tag=TAG).info(self.output.strip())


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        self.interface_type = InterfaceType.LOCAL
        self.model_dir = config.get("model_dir")
        self.output_dir = config.get("output_dir")
        self.delete_audio_file = delete_audio_file
        # careconnect: language for Whisper decoding (English by default so the
        # physical Watcher's English speech is transcribed as English, not CJK).
        self.language = config.get("language", "en")

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

        # careconnect: prefer an English Whisper export if one is present in
        # model_dir. SenseVoiceSmall cannot do English reliably; Whisper can.
        whisper = _detect_whisper_model(self.model_dir)
        if whisper is not None:
            logger.bind(tag=TAG).info(
                f"检测到 sherpa-onnx Whisper 模型 (language={self.language}): "
                f"encoder={os.path.basename(whisper['encoder'])}, "
                f"decoder={os.path.basename(whisper['decoder'])}"
            )
            with CaptureOutput():
                self.model = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=whisper["encoder"],
                    decoder=whisper["decoder"],
                    tokens=whisper["tokens"],
                    language=self.language,
                    task="transcribe",
                    num_threads=2,
                    decoding_method="greedy_search",
                    debug=False,
                )
            return

        # --- Fallback: SenseVoice export (original upstream behaviour) ---
        # 初始化模型文件路径
        model_files = {
            "model.int8.onnx": os.path.join(self.model_dir, "model.int8.onnx"),
            "tokens.txt": os.path.join(self.model_dir, "tokens.txt"),
        }

        # 下载并检查模型文件
        try:
            for file_name, file_path in model_files.items():
                if not os.path.isfile(file_path):
                    logger.bind(tag=TAG).info(f"正在下载模型文件: {file_name}")
                    model_file_download(
                        model_id="pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue",
                        file_path=file_name,
                        local_dir=self.model_dir,
                    )

                    if not os.path.isfile(file_path):
                        raise FileNotFoundError(f"模型文件下载失败: {file_path}")

            self.model_path = model_files["model.int8.onnx"]
            self.tokens_path = model_files["tokens.txt"]

        except Exception as e:
            logger.bind(tag=TAG).error(f"模型文件处理失败: {str(e)}")
            raise

        with CaptureOutput():
            self.model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=self.model_path,
                tokens=self.tokens_path,
                num_threads=2,
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                debug=False,
                use_itn=True,
            )

    def read_wave(self, wave_filename: str) -> Tuple[np.ndarray, int]:
        """
        Args:
        wave_filename:
            Path to a wave file. It should be single channel and each sample should
            be 16-bit. Its sample rate does not need to be 16kHz.
        Returns:
        Return a tuple containing:
        - A 1-D array of dtype np.float32 containing the samples, which are
        normalized to the range [-1, 1].
        - sample rate of the wave file
        """

        with wave.open(wave_filename) as f:
            assert f.getnchannels() == 1, f.getnchannels()
            assert f.getsampwidth() == 2, f.getsampwidth()  # it is in bytes
            num_samples = f.getnframes()
            samples = f.readframes(num_samples)
            samples_int16 = np.frombuffer(samples, dtype=np.int16)
            samples_float32 = samples_int16.astype(np.float32)

            samples_float32 = samples_float32 / 32768
            return samples_float32, f.getframerate()

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, audio_format="opus"
    ) -> Tuple[Optional[str], Optional[str]]:
        """语音转文本主处理逻辑"""
        file_path = None
        try:
            # 保存音频文件
            start_time = time.time()
            if audio_format == "pcm":
                pcm_data = opus_data
            else:
                pcm_data = self.decode_opus(opus_data)
            file_path = self.save_audio_to_file(pcm_data, session_id)
            logger.bind(tag=TAG).debug(
                f"音频文件保存耗时: {time.time() - start_time:.3f}s | 路径: {file_path}"
            )

            # 语音识别
            start_time = time.time()
            s = self.model.create_stream()
            samples, sample_rate = self.read_wave(file_path)
            s.accept_waveform(sample_rate, samples)
            self.model.decode_stream(s)
            text = s.result.text
            logger.bind(tag=TAG).debug(
                f"语音识别耗时: {time.time() - start_time:.3f}s | 结果: {text}"
            )

            return text, file_path

        except Exception as e:
            logger.bind(tag=TAG).error(f"语音识别失败: {e}", exc_info=True)
            return "", file_path
        finally:
            # 文件清理逻辑
            if self.delete_audio_file and file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.bind(tag=TAG).debug(f"已删除临时音频文件: {file_path}")
                except Exception as e:
                    logger.bind(tag=TAG).error(f"文件删除失败: {file_path} | 错误: {e}")
