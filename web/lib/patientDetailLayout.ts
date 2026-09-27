/** Client detail three-pane widths. Desktop is ~26 / 48 / 26. Stacks below xl. */

export const PATIENT_DETAIL_SECTION =
  "flex flex-col xl:flex-row min-h-[calc(100vh-9rem)] xl:h-[calc(100vh-9rem)] xl:overflow-hidden";

export const PATIENT_DETAIL_LEFT =
  "w-full xl:w-[26%] xl:max-w-[26%] xl:shrink-0 border-b xl:border-b-0 xl:border-r border-slate-line/70 bg-bone-soft/60 flex flex-col xl:h-full xl:min-h-0";

export const PATIENT_DETAIL_CENTER =
  "flex-1 min-w-0 xl:w-[48%] px-6 md:px-10 py-5 overflow-y-auto bg-white xl:h-full xl:min-h-0";

export const PATIENT_DETAIL_RIGHT =
  "w-full xl:w-[26%] xl:max-w-[26%] xl:shrink-0 border-t xl:border-t-0 xl:border-l border-slate-line/70 bg-bone-soft/60 px-5 py-5 transition xl:h-full xl:min-h-0 overflow-hidden flex flex-col";
