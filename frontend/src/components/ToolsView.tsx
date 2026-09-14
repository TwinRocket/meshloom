import { useTranslation } from 'react-i18next';
import {
  List,
  Waypoints,
  Waypoints as Trace,
  Crosshair,
  Search,
  Unlock,
  CheckCheck,
} from 'lucide-react';
import type { Conversation, HealthStatus } from '../types';
import { RadioStatusChip } from './RadioStatusChip';

/**
 * Everything that is not a conversation, as a screen.
 *
 * These lived in a section of the drawer. With the drawer gone on phones they need a
 * home of their own, and a list of destinations with a line each is more use than a
 * column of icons was: the names alone do not say what the packet feed or the
 * channel finder are for.
 */

type ToolId = 'raw' | 'visualizer' | 'trace' | 'locate' | 'search';

interface Props {
  onSelectConversation: (conversation: Conversation) => void;
  onToggleCracker: () => void;
  onMarkAllRead: () => void;
  crackerVisible: boolean;
  health?: HealthStatus | null;
  onOpenRadioSettings?: () => void;
}

const TOOLS: { id: ToolId; labelKey: string; descriptionKey: string; Icon: typeof List }[] = [
  {
    id: 'raw',
    labelKey: 'sidebar.packetFeed',
    descriptionKey: 'toolsView.packetFeedDescription',
    Icon: List,
  },
  {
    id: 'visualizer',
    labelKey: 'sidebar.meshVisualizer',
    descriptionKey: 'toolsView.visualizerDescription',
    Icon: Waypoints,
  },
  {
    id: 'trace',
    labelKey: 'sidebar.trace',
    descriptionKey: 'toolsView.traceDescription',
    Icon: Trace,
  },
  {
    id: 'locate',
    labelKey: 'locate.title',
    descriptionKey: 'toolsView.locateDescription',
    Icon: Crosshair,
  },
  {
    id: 'search',
    labelKey: 'sidebar.messageSearch',
    descriptionKey: 'toolsView.searchDescription',
    Icon: Search,
  },
];

const ROW_CLASS =
  'flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring';

export function ToolsView({
  onSelectConversation,
  onToggleCracker,
  onMarkAllRead,
  crackerVisible,
  health,
  onOpenRadioSettings,
}: Props) {
  const { t } = useTranslation();

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Opaque and clear of the top edge, for the same reason as the list: iOS
          blurs what reaches into the strip under the status bar. */}
      <div className="flex shrink-0 items-center gap-2 bg-background px-4 pb-2 pt-5">
        <h1 className="text-2xl font-semibold tracking-tight">{t('toolsView.title')}</h1>
        <RadioStatusChip
          health={health ?? null}
          onOpenRadioSettings={onOpenRadioSettings}
          className="ml-auto max-w-[9rem]"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <ul>
          {TOOLS.map(({ id, labelKey, descriptionKey, Icon }) => (
            <li key={id}>
              <button
                type="button"
                className={ROW_CLASS}
                onClick={() => onSelectConversation({ type: id, id, name: t(labelKey) })}
              >
                <span
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
                  aria-hidden="true"
                >
                  <Icon className="h-5 w-5" />
                </span>
                <span className="flex min-w-0 flex-col">
                  <span className="font-medium">{t(labelKey)}</span>
                  <span className="text-sm text-muted-foreground">{t(descriptionKey)}</span>
                </span>
              </button>
            </li>
          ))}

          <li>
            <button type="button" className={ROW_CLASS} onClick={onToggleCracker}>
              <span
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
                aria-hidden="true"
              >
                <Unlock className="h-5 w-5" />
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="font-medium">
                  {crackerVisible ? t('sidebar.hideChannelFinder') : t('sidebar.showChannelFinder')}
                </span>
                <span className="text-sm text-muted-foreground">
                  {t('toolsView.crackerDescription')}
                </span>
              </span>
            </button>
          </li>

          <li>
            <button type="button" className={ROW_CLASS} onClick={onMarkAllRead}>
              <span
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
                aria-hidden="true"
              >
                <CheckCheck className="h-5 w-5" />
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="font-medium">{t('sidebar.markAllRead')}</span>
                <span className="text-sm text-muted-foreground">
                  {t('toolsView.markAllReadDescription')}
                </span>
              </span>
            </button>
          </li>
        </ul>
      </div>
    </div>
  );
}
