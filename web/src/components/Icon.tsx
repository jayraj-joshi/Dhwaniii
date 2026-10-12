import type { ReactNode } from "react";

const solid = (d: ReactNode) => ({ solid: true, d });
const line = (d: ReactNode) => ({ solid: false, d });

const ICONS = {
  home: line(<path d="M4 11.5 12 4.5l8 7M6 10v9.5h4.5V14h3v5.5H18V10" />),
  search: line(<><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5" /></>),
  library: line(<path d="M5 4v16M10 4v16M15.2 5.5l4.2 14.2" />),
  play: solid(<path d="M8 5.2v13.6c0 .5.5.8.9.5l10.8-6.8c.4-.3.4-.8 0-1L8.9 4.7c-.4-.3-.9 0-.9.5Z" />),
  pause: solid(<><rect x="6" y="5" width="4" height="14" rx="1.2" /><rect x="14" y="5" width="4" height="14" rx="1.2" /></>),
  next: solid(<><path d="M5.5 5.8v12.4c0 .5.5.7.9.4l8.4-6.2c.3-.2.3-.6 0-.8L6.4 5.4c-.4-.3-.9-.1-.9.4Z" /><rect x="16.5" y="5" width="2.4" height="14" rx="1.1" /></>),
  prev: solid(<g transform="translate(24 0) scale(-1 1)"><path d="M5.5 5.8v12.4c0 .5.5.7.9.4l8.4-6.2c.3-.2.3-.6 0-.8L6.4 5.4c-.4-.3-.9-.1-.9.4Z" /><rect x="16.5" y="5" width="2.4" height="14" rx="1.1" /></g>),
  shuffle: line(<path d="M3 7h3c2.2 0 3.4 1 4.6 2.8l2.8 4.4C14.600 16 15.800 17 18 17h3M3 17h3c1.200 0 2.200-.4 3-1.200M13.600 8.600C14.600 7.500 15.800 7 17.400 7H21M18.500 4.500 21 7l-2.500 2.500M18.500 14.500 21 17l-2.500 2.500" />),
  repeat: line(<path d="M4 11V9.500A3.500 3.500 0 0 1 7.500 6H20M17 3l3 3-3 3M20 13v1.500a3.500 3.500 0 0 1-3.500 3.500H4M7 21l-3-3 3-3" />),
  "repeat-one": line(<><path d="M4 11V9.500A3.500 3.500 0 0 1 7.500 6H20M17 3l3 3-3 3M20 13v1.500a3.500 3.500 0 0 1-3.500 3.500H4M7 21l-3-3 3-3" /><path d="M11 10.500l1.500-1v5.500" strokeWidth="1.600" /></>),
  heart: line(<path d="M12 20s-7.500-4.600-7.500-10.200A4.300 4.300 0 0 1 12 7.300a4.300 4.300 0 0 1 7.500 2.500C19.500 15.400 12 20 12 20Z" />),
  "heart-fill": solid(<path d="M12 20.500s-8-4.800-8-10.800A4.700 4.700 0 0 1 12 6.600a4.700 4.700 0 0 1 8 3.100c0 6-8 10.800-8 10.800Z" />),
  more: solid(<><circle cx="5.500" cy="12" r="1.700" /><circle cx="12" cy="12" r="1.700" /><circle cx="18.500" cy="12" r="1.700" /></>),
  plus: line(<path d="M12 5v14M5 12h14" />),
  down: line(<path d="m6 9.500 6 6 6-6" />),
  right: line(<path d="m9.500 6 6 6-6 6" />),
  back: line(<path d="m14.500 5.500-6.500 6.500 6.500 6.500" />),
  close: line(<path d="m6 6 12 12M18 6 6 18" />),
  check: line(<path d="m5 12.500 4.500 4.500L19 7.500" />),
  queue: line(<path d="M4 6h12M4 11h8M4 16h6M15 14.500v6l5-3Z" />),
  cloud: line(<path d="M7.500 18.500a4 4 0 0 1-.7-7.900 5.500 5.500 0 0 1 10.600 1.200 3.400 3.400 0 0 1-.4 6.700Z" />),
  warning: line(<path d="M12 4.500 21 19.500H3ZM12 10v4.500M12 17v.1" />),
  settings: line(<><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></>),
  trash: line(<path d="M5 7h14M9.500 7V4.500h5V7M7 7l.8 12.500h8.400L17 7M10 11v5M14 11v5" />),
  edit: line(<path d="m5 19 .8-3.800L16 5l3 3L8.800 18.200ZM14 7l3 3" />),
  pin: line(<path d="m14.500 4 5.500 5.500-2.200.8-3.300 3.300.3 3.900-1.200 1.200-3.500-3.500-4.800 4.800M9.800 9.700 14.500 4" />),
  user: line(<><circle cx="12" cy="8.500" r="3.500" /><path d="M5 20c.6-3.600 3.400-5.500 7-5.500s6.400 1.900 7 5.500" /></>),
  volume: line(<path d="M4 10v4h3.500l4.500 3.500v-11L7.500 10ZM15.500 9.500a3.500 3.500 0 0 1 0 5M18 7a7 7 0 0 1 0 10" />),
  image: line(<><rect x="4" y="5" width="16" height="14" rx="2" /><path d="m4 16 4.500-4.500L13 16l3-3 4 4" /><circle cx="15.500" cy="9.500" r="1.200" /></>),
  up: line(<path d="m6 14.500 6-6 6 6" />),
  upload: line(<path d="M12 16V5M7.500 9.500 12 5l4.500 4.500M5 19h14" />),
} as const;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 24 }: { name: IconName; size?: number }) {
  const { solid: filled, d } = ICONS[name];
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false"
      fill={filled ? "currentColor" : "none"} stroke={filled ? "none" : "currentColor"} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {d}
    </svg>
  );
}
