/** Typography + layout blocks aligned with Dashboard (Home). */

export const pg = {

  title: 'text-[28px] font-semibold text-white leading-none',

  subtitle: 'text-[13px] font-semibold text-[#b6baf2]',

  filterTab: 'text-[13px] font-semibold leading-none',

  filterTabActive: 'text-[#006bf9]',

  filterTabIdle: 'text-[#b6baf2]',

  section: 'text-[12px] font-semibold tracking-[0.12em] text-[#006bf9]',

  sectionColored: 'text-[15px] font-semibold tracking-wide',

  cardTitle: 'text-[13px] font-semibold text-white leading-tight',

  cardTitleMd: 'text-[14px] font-semibold text-white leading-tight',

  cardMeta: 'text-[12px] font-medium text-[#b6baf2]',

  cardMetaSm: 'text-[12px] font-medium text-[#b6baf2]',

  body: 'text-[13px] font-medium text-white leading-relaxed',

  bodyLg: 'text-[14px] font-medium text-white leading-relaxed',

  toolbar: 'text-[13px] font-medium',

  search: 'text-[13px] font-semibold',

  empty: 'text-[13px] font-medium text-[#9ba2b2]',

  labelAccent: 'text-[11px] font-semibold uppercase tracking-[0.18em] text-[#006bf9]',

  heroTitle: 'text-[19px] font-bold text-white leading-tight',

  heroSub: 'text-[13px] font-bold text-[#b6baf2]',

  promptTitle: 'text-[13px] font-semibold text-white',

  promptDetail: 'text-[12px] font-medium text-[#b6baf2]',

} as const



/** Shared spacing / radii / chrome — matches Home card scale (~15% smaller than prior branch pages). */

export const blk = {

  pagePad: 'px-5 pt-4 pb-12',

  chromeRow: 'mb-3 flex justify-end items-center gap-2',

  avatar: 'h-[38px] w-[38px]',

  avatarBtn: 'flex h-[38px] w-[38px] items-center justify-center rounded-full border border-[#21284b] bg-gradient-to-b from-[#000f33] to-[#000a26]',

  notifIcon: 22,

  headerCard: 'mb-4 overflow-hidden rounded-[18px] border border-[#3f4253] bg-gradient-to-b from-[#02123c] to-[#000a26] px-5 py-4 min-h-[100px]',

  filterBar: 'mb-5 flex min-h-[75px] flex-wrap items-center gap-2 overflow-x-auto rounded-[18px] border border-[#3f4253] bg-[#010817] px-4 py-2.5',

  filterTabPad: 'rounded-[14px] px-4 py-2',

  pane: 'rounded-[18px] border border-[#3f4253] bg-gradient-to-b from-[#000f33] to-[#000a26]',

  paneMinH: 'min-h-[408px] lg:min-h-[578px]',

  paneMinHDetail: 'min-h-[408px] lg:min-h-[520px]',

  gridGap: 'gap-3 lg:gap-3',

  row: 'rounded-[12px] px-2.5 py-2',

  rowGap: 'gap-2',

  listInner: 'px-3 py-2',

  listSectionGap: 'gap-2',

  searchWrap: 'lg:w-[255px]',

  searchInput: 'rounded-[12px] border border-[#21284b] bg-[#01071c]/40 py-2 pl-9 pr-3',

  iconDot: 10,

  addTaskBtn: 'inline-flex h-[75px] shrink-0 items-center justify-center gap-2 rounded-[14px] border border-[#21284b] bg-gradient-to-b from-[#011137] to-[#000a26] px-5 sm:min-w-[228px]',

  addTaskIcon: 28,

  meetingIconBox: 'flex h-[48px] w-[48px] shrink-0 items-center justify-center rounded-[14px] border border-[#3f4253] bg-[#010b26]',

  meetingIcon: 32,

  assistantSidebar: 'lg:w-[336px]',

  assistantIntro: 'rounded-[18px] border border-[#3f8cff] bg-gradient-to-b from-[#011137] to-[#000a26] px-5 pb-6 pt-6 min-h-[238px]',

  assistantPrompt: 'rounded-[17px] border border-[#3f8cff] bg-gradient-to-b from-[#011137] to-[#000a26] p-4 min-h-[122px]',

  assistantMain: 'rounded-[18px] border border-[#3f4253] bg-gradient-to-b from-[#011137] to-[#000a26] lg:min-h-[442px]',

  composer: 'rounded-[14px] border-2 border-[#3f4253] bg-gradient-to-b from-[#011137] to-[#000a26] min-h-[75px] sm:min-h-[94px]',

} as const


