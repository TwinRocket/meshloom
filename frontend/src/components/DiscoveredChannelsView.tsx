import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Hash, Unlock, Ban } from 'lucide-react';

import type { Channel, Conversation } from '../types';
import { pendingChannels } from '../utils/channelMembership';
import { getStateKey } from '../utils/conversationState';
import { cn } from '../lib/utils';
import { Button } from './ui/button';
import { ToolPaneHeader } from './ToolPaneHeader';
import { RejectedChannelsModal } from './RejectedChannelsModal';

interface Props {
  channels: Channel[];
  unreadCounts?: Record<string, number>;
  lastMessageTimes?: Record<string, number>;
  lastMessagePreviews?: Record<string, string>;
  onSelectConversation: (conversation: Conversation) => void;
  onBackToTools?: () => void;
  onToggleCracker: () => void;
  crackerVisible: boolean;
  crackerQueueCount?: number;
  onAdoptedRejected: (key: string, name: string) => void;
}

export function DiscoveredChannelsView({
  channels,
  unreadCounts = {},
  lastMessageTimes = {},
  lastMessagePreviews = {},
  onSelectConversation,
  onBackToTools,
  onToggleCracker,
  crackerVisible,
  crackerQueueCount = 0,
  onAdoptedRejected,
}: Props) {
  const { t } = useTranslation();
  const [rejectedOpen, setRejectedOpen] = useState(false);
  const pending = useMemo(() => pendingChannels(channels), [channels]);

  const rows = useMemo(() => {
    return pending
      .map((channel) => {
        const stateKey = getStateKey('channel', channel.key);
        return {
          channel,
          unread: unreadCounts[stateKey] ?? 0,
          lastAt: lastMessageTimes[stateKey] ?? 0,
          preview: lastMessagePreviews[stateKey] ?? '',
        };
      })
      .sort((a, b) => {
        if (a.lastAt !== b.lastAt) return b.lastAt - a.lastAt;
        return a.channel.name.localeCompare(b.channel.name);
      });
  }, [pending, unreadCounts, lastMessageTimes, lastMessagePreviews]);

  return (
    <>
      <ToolPaneHeader
        title={t('discovered.title')}
        onBack={onBackToTools}
        actions={
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 px-2.5 text-xs"
              onClick={onToggleCracker}
            >
              <Unlock className="h-3.5 w-3.5" aria-hidden="true" />
              {crackerVisible ? t('sidebar.hideChannelFinder') : t('sidebar.showChannelFinder')}
              {crackerQueueCount > 0 ? ` (${crackerQueueCount})` : ''}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 px-2.5 text-xs"
              onClick={() => setRejectedOpen(true)}
            >
              <Ban className="h-3.5 w-3.5" aria-hidden="true" />
              {t('discovered.rejected')}
            </Button>
          </div>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">
            {t('discovered.empty')}
          </p>
        ) : (
          <ul>
            {rows.map(({ channel, unread, preview }) => (
              <li key={channel.key}>
                <button
                  type="button"
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  onClick={() =>
                    onSelectConversation({
                      type: 'channel',
                      id: channel.key,
                      name: channel.name,
                    })
                  }
                >
                  <span
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
                    aria-hidden="true"
                  >
                    <Hash className="h-5 w-5" />
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5 border-b border-border/20 pb-2.5">
                    <span className="truncate font-medium">{channel.name}</span>
                    <span className="truncate text-sm text-muted-foreground">
                      {preview || t('conversationList.noMessages')}
                    </span>
                  </span>
                  {unread > 0 && (
                    <span
                      className={cn(
                        'shrink-0 rounded-full bg-primary px-1.5 py-0.5 text-[0.6875rem] font-semibold text-primary-foreground'
                      )}
                    >
                      {unread > 99 ? '99+' : unread}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <RejectedChannelsModal
        open={rejectedOpen}
        onClose={() => setRejectedOpen(false)}
        onAdopted={(key, name) => {
          setRejectedOpen(false);
          onAdoptedRejected(key, name);
        }}
      />
    </>
  );
}
