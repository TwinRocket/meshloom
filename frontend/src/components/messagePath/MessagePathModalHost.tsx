import { forwardRef, useCallback, useImperativeHandle, useState } from 'react';
import type { Contact, MessagePath, RadioConfig } from '../../types';
import type { SenderInfo } from '../../utils/pathUtils';
import { PathModal } from '../PathModal';

export type MessagePathSelection = {
  paths: MessagePath[];
  senderInfo: SenderInfo;
  messageId?: number;
  packetId?: number | null;
  isOutgoingChan?: boolean;
};

export type MessagePathModalHostHandle = {
  open: (selection: MessagePathSelection) => void;
};

interface MessagePathModalHostProps {
  contacts: Contact[];
  config: RadioConfig | null;
  resendableIds: Set<number>;
  /** Community directory available: unknown hops may be looked up there. */
  directoryEnabled?: boolean;
  onResend?: (messageId: number, newTimestamp?: boolean) => void;
  onAnalyzePacket?: (messageId?: number) => void;
}

export const MessagePathModalHost = forwardRef<
  MessagePathModalHostHandle,
  MessagePathModalHostProps
>(function MessagePathModalHost(
  { contacts, config, resendableIds, directoryEnabled = false, onResend, onAnalyzePacket },
  ref
) {
  const [selectedPath, setSelectedPath] = useState<MessagePathSelection | null>(null);

  const open = useCallback((selection: MessagePathSelection) => {
    setSelectedPath(selection);
  }, []);

  useImperativeHandle(ref, () => ({ open }), [open]);

  const isSelectedMessageResendable =
    selectedPath?.messageId !== undefined && resendableIds.has(selectedPath.messageId);

  if (!selectedPath) {
    return null;
  }

  return (
    <PathModal
      open={true}
      onClose={() => setSelectedPath(null)}
      paths={selectedPath.paths}
      senderInfo={selectedPath.senderInfo}
      contacts={contacts}
      config={config}
      directoryEnabled={directoryEnabled}
      messageId={selectedPath.messageId}
      packetId={selectedPath.packetId}
      isOutgoingChan={selectedPath.isOutgoingChan}
      isResendable={isSelectedMessageResendable}
      onResend={onResend}
      onAnalyzePacket={
        selectedPath.packetId != null
          ? () => {
              onAnalyzePacket?.(selectedPath.messageId);
            }
          : undefined
      }
    />
  );
});
