import {
  BarChart3,
  PanelLeft,
  Bell,
  Database,
  Globe,
  Info,
  MonitorCog,
  RadioTower,
  Share2,
  SlidersHorizontal,
  Waypoints,
  type LucideIcon,
} from 'lucide-react';

export type SettingsSection =
  | 'radio'
  | 'proxy'
  | 'local'
  | 'notifications'
  | 'community'
  | 'radio-app'
  | 'database'
  | 'fanout'
  | 'statistics'
  | 'navigation'
  | 'about';

export const SETTINGS_SECTION_ORDER: SettingsSection[] = [
  'radio',
  'proxy',
  'local',
  'notifications',
  'community',
  'fanout',
  'radio-app',
  'database',
  'navigation',
  'statistics',
  'about',
];

/** i18n key ids. Translate at render with t(SETTINGS_SECTION_LABELS[section]). */
export const SETTINGS_SECTION_LABELS: Record<SettingsSection, string> = {
  radio: 'settingsNav.radio',
  proxy: 'settingsNav.proxy',
  local: 'settingsNav.local',
  notifications: 'settingsNav.notifications',
  community: 'settingsNav.community',
  'radio-app': 'settingsNav.radioApp',
  database: 'settingsNav.database',
  fanout: 'settingsNav.fanout',
  navigation: 'settingsNav.navigation',
  statistics: 'settingsNav.statistics',
  about: 'settingsNav.about',
};

export const SETTINGS_SECTION_ICONS: Record<SettingsSection, LucideIcon> = {
  radio: RadioTower,
  proxy: Waypoints,
  local: MonitorCog,
  notifications: Bell,
  community: Globe,
  'radio-app': SlidersHorizontal,
  database: Database,
  fanout: Share2,
  navigation: PanelLeft,
  statistics: BarChart3,
  about: Info,
};
