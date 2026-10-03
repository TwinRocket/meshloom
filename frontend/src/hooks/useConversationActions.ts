import { useCallback, type MutableRefObject, type RefObject } from 'react';
import { api, formatApiError } from '../api';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import type { MessageInputHandle } from '../components/MessageInput';
import type { Channel, Contact, Conversation, Message, PathDiscoveryResponse } from '../types';
import { mergeContactIntoList } from '../utils/contactMerge';

interface UseConversationActionsArgs {
  activeConversation: Conversation | null;
  activeConversationRef: MutableRefObject<Conversation | null>;
  setContacts: React.Dispatch<React.SetStateAction<Contact[]>>;
  setChannels: React.Dispatch<React.SetStateAction<Channel[]>>;
  observeMessage: (msg: Message) => { added: boolean; activeConversation: boolean };
  messageInputRef: RefObject<MessageInputHandle | null>;
  activeRadioId?: string;
}

interface UseConversationActionsResult {
  handleSendMessage: (text: string) => Promise<void>;
  handleResendChannelMessage: (messageId: number, newTimestamp?: boolean) => Promise<void>;
  handleSetChannelFloodScopeOverride: (
    channelKey: string,
    floodScopeOverride: string
  ) => Promise<void>;
  handleSetChannelPathHashModeOverride: (
    channelKey: string,
    pathHashModeOverride: number | null
  ) => Promise<void>;
  handleSenderClick: (sender: string, quote?: string) => void;
  handleTrace: () => Promise<void>;
  handlePathDiscovery: (publicKey: string) => Promise<PathDiscoveryResponse>;
}

export function useConversationActions({
  activeConversation,
  activeConversationRef,
  setContacts,
  setChannels,
  observeMessage,
  messageInputRef,
  activeRadioId,
}: UseConversationActionsArgs): UseConversationActionsResult {
  const mergeChannelIntoList = useCallback(
    (updated: Channel) => {
      setChannels((prev) => {
        const existingIndex = prev.findIndex((channel) => channel.key === updated.key);
        if (existingIndex === -1) {
          return [...prev, updated].sort((a, b) => a.name.localeCompare(b.name));
        }
        const next = [...prev];
        next[existingIndex] = updated;
        return next;
      });
    },
    [setChannels]
  );

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeConversation) return;

      const conversationId = activeConversation.id;
      const sent =
        activeConversation.type === 'channel'
          ? activeRadioId
            ? await api.sendChannelMessage(activeConversation.id, text, activeRadioId)
            : await api.sendChannelMessage(activeConversation.id, text)
          : activeRadioId
            ? await api.sendDirectMessage(activeConversation.id, text, activeRadioId)
            : await api.sendDirectMessage(activeConversation.id, text);

      if (activeConversationRef.current?.id === conversationId) {
        observeMessage(sent);
      }
    },
    [activeConversation, activeConversationRef, observeMessage, activeRadioId]
  );

  const handleResendChannelMessage = useCallback(
    async (messageId: number, newTimestamp?: boolean) => {
      try {
        const resent = await api.resendChannelMessage(messageId, newTimestamp);
        const resentMessage = resent.message;
        if (
          newTimestamp &&
          resentMessage &&
          activeConversationRef.current?.type === 'channel' &&
          activeConversationRef.current.id === resentMessage.conversation_key
        ) {
          observeMessage(resentMessage);
        }
        toast.success(newTimestamp ? i18n.t('toast.resentNewTimestamp') : i18n.t('toast.resent'));
      } catch (err) {
        toast.error(i18n.t('toast.resendFailed'), {
          description: formatApiError(err, i18n.t),
        });
      }
    },
    [activeConversationRef, observeMessage]
  );

  const handleSetChannelFloodScopeOverride = useCallback(
    async (channelKey: string, floodScopeOverride: string) => {
      try {
        const updated = await api.setChannelFloodScopeOverride(channelKey, floodScopeOverride);
        mergeChannelIntoList(updated);
        toast.success(
          updated.flood_scope_override
            ? i18n.t('toast.regionalOverrideSaved')
            : i18n.t('toast.regionalOverrideCleared')
        );
      } catch (err) {
        toast.error(i18n.t('toast.regionalOverrideFailed'), {
          description: formatApiError(err, i18n.t),
        });
      }
    },
    [mergeChannelIntoList]
  );

  const handleSetChannelPathHashModeOverride = useCallback(
    async (channelKey: string, pathHashModeOverride: number | null) => {
      try {
        const updated = await api.setChannelPathHashModeOverride(channelKey, pathHashModeOverride);
        mergeChannelIntoList(updated);
        toast.success(
          updated.path_hash_mode_override != null
            ? i18n.t('toast.pathHopOverrideSaved')
            : i18n.t('toast.pathHopOverrideCleared')
        );
      } catch (err) {
        toast.error(i18n.t('toast.pathHopOverrideFailed'), {
          description: formatApiError(err, i18n.t),
        });
      }
    },
    [mergeChannelIntoList]
  );

  const handleSenderClick = useCallback(
    (sender: string, quote?: string) => {
      if (quote != null) {
        messageInputRef.current?.startReply(sender, quote);
        return;
      }
      messageInputRef.current?.appendText(`@[${sender}] `);
    },
    [messageInputRef]
  );

  const handleTrace = useCallback(async () => {
    if (!activeConversation || activeConversation.type !== 'contact') return;
    toast(i18n.t('toast.traceStarted'));
    try {
      const result = await api.requestTrace(activeConversation.id);
      const parts: string[] = [];
      if (result.remote_snr !== null) {
        parts.push(i18n.t('toast.traceRemoteSnr', { snr: result.remote_snr.toFixed(1) }));
      }
      if (result.local_snr !== null) {
        parts.push(i18n.t('toast.traceLocalSnr', { snr: result.local_snr.toFixed(1) }));
      }
      const detail = parts.join(', ');
      toast.success(
        detail ? i18n.t('toast.traceCompleteDetail', { detail }) : i18n.t('toast.traceComplete')
      );
    } catch (err) {
      toast.error(i18n.t('toast.traceFailed'), {
        description: formatApiError(err, i18n.t),
      });
    }
  }, [activeConversation]);

  const handlePathDiscovery = useCallback(
    async (publicKey: string) => {
      const result = await api.requestPathDiscovery(publicKey);
      setContacts((prev) => mergeContactIntoList(prev, result.contact));
      return result;
    },
    [setContacts]
  );

  return {
    handleSendMessage,
    handleResendChannelMessage,
    handleSetChannelFloodScopeOverride,
    handleSetChannelPathHashModeOverride,
    handleSenderClick,
    handleTrace,
    handlePathDiscovery,
  };
}
