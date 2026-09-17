/**
 * Tests for MessageInput component.
 *
 * Verifies character/byte limit calculation, warning states, and send button
 * behavior for both DM and channel conversations.
 */

import { createRef } from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { MessageInput, type MessageInputHandle } from '../components/MessageInput';
import i18n from '../i18n';
import { toast } from '../components/ui/sonner';
import {
  conversationDraftStorageKey,
  loadConversationDraft,
  saveConversationDraft,
} from '../utils/conversationDrafts';

// Mock sonner (toast)
vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockToast = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

const textEncoder = new TextEncoder();

function byteLen(s: string): number {
  return textEncoder.encode(s).length;
}

describe('MessageInput', () => {
  const onSend = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderInput(props: {
    conversationType?: 'contact' | 'channel' | 'raw';
    senderName?: string;
    disabled?: boolean;
  }) {
    return render(
      <MessageInput
        onSend={onSend}
        disabled={props.disabled ?? false}
        conversationType={props.conversationType}
        senderName={props.senderName}
        placeholder="Type a message..."
      />
    );
  }

  function getInput() {
    return document.querySelector('textarea[name="chat-message-input"]') as HTMLTextAreaElement;
  }

  function getSendButton() {
    return screen.getByRole('button', { name: i18n.t('chat.send') }) as HTMLButtonElement;
  }

  describe('send button state', () => {
    it('is disabled when text is empty', () => {
      renderInput({ conversationType: 'contact' });
      expect(getSendButton()).toBeDisabled();
    });

    it('is enabled when text is entered', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      expect(getSendButton()).toBeEnabled();
    });

    it('is disabled when whitespace-only', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: '   ' } });
      expect(getSendButton()).toBeDisabled();
    });

    it('is disabled when disabled prop is true', () => {
      renderInput({ conversationType: 'contact', disabled: true });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      expect(getSendButton()).toBeDisabled();
    });

    it('exposes a single attach trigger for composer tools', () => {
      renderInput({ conversationType: 'contact' });
      expect(screen.getByTestId('composer-attach-trigger')).toBeInTheDocument();
      expect(screen.queryByTestId('composer-more-trigger')).toBeNull();
    });

    it('does not send on Enter so the mobile keyboard can insert a newline', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      fireEvent.keyDown(getInput(), { key: 'Enter' });
      expect(onSend).not.toHaveBeenCalled();
      expect(getInput().value).toBe('Hello');
    });
  });

  describe('byte counter display', () => {
    it('shows byte counter for DM conversations', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });

      // Should show "5/156" somewhere (DM hard limit = 156)
      expect(screen.getByText(/5\/156/)).toBeTruthy();
    });

    it('shows byte counter for channel conversations', () => {
      renderInput({ conversationType: 'channel', senderName: 'MyNode' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });

      // Channel hard limit = 156 - byteLen("MyNode") - 2 = 156 - 6 - 2 = 148
      expect(screen.getByText(/5\/148/)).toBeTruthy();
    });

    it('does not show byte counter for raw conversations', () => {
      renderInput({ conversationType: 'raw' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });

      // No counter should be visible
      expect(screen.queryByText(/\/\d+/)).toBeNull();
    });

    it('accounts for multi-byte characters in byte count', () => {
      renderInput({ conversationType: 'contact' });
      // Emoji: "🥝" is 4 bytes in UTF-8
      fireEvent.change(getInput(), { target: { value: '🥝' } });
      const bytes = byteLen('🥝'); // Should be 4
      expect(bytes).toBe(4);
      expect(screen.getByText(new RegExp(`${bytes}/156`))).toBeTruthy();
    });

    it('shows the mobile counter from the UTF-8 warning threshold, not character count', () => {
      renderInput({ conversationType: 'contact' });
      // 101 ASCII chars used to open the mobile counter; warning threshold is 140 bytes.
      fireEvent.change(getInput(), { target: { value: 'x'.repeat(101) } });
      expect(screen.getAllByText(/101\/156/)).toHaveLength(1);

      // 70 × "é" = 140 bytes / 70 chars — below the old char>100 gate, at the warning threshold.
      fireEvent.change(getInput(), { target: { value: 'é'.repeat(70) } });
      expect(screen.getAllByText(/140\/156/)).toHaveLength(2);
    });
  });

  describe('channel limit adjusts for sender name', () => {
    it('reduces limit based on sender name byte length', () => {
      // Sender name "LongNodeName" = 12 bytes + 2 for ": " = 14 overhead
      // Hard limit = 156 - 14 = 142
      renderInput({ conversationType: 'channel', senderName: 'LongNodeName' });
      fireEvent.change(getInput(), { target: { value: 'x' } });
      expect(screen.getByText(/1\/142/)).toBeTruthy();
    });

    it('uses default 10-byte name when sender name is absent', () => {
      // Default: 10 bytes + 2 = 12 overhead. Hard limit = 156 - 12 = 144
      renderInput({ conversationType: 'channel' });
      fireEvent.change(getInput(), { target: { value: 'x' } });
      expect(screen.getByText(/1\/144/)).toBeTruthy();
    });

    it('handles multi-byte sender names correctly', () => {
      // "🥝Node" = 4 + 4 = 8 bytes name + 2 separator = 10 overhead
      // Hard limit = 156 - 10 = 146
      const senderName = '🥝Node';
      const nameBytes = byteLen(senderName);
      const expectedLimit = 156 - nameBytes - 2;
      renderInput({ conversationType: 'channel', senderName });
      fireEvent.change(getInput(), { target: { value: 'x' } });
      expect(screen.getByText(new RegExp(`1/${expectedLimit}`))).toBeTruthy();
    });
  });

  describe('warning states', () => {
    it('shows warning text when exceeding DM warning threshold', () => {
      renderInput({ conversationType: 'contact' });
      // DM warning threshold = 140 bytes
      const text = 'x'.repeat(141);
      fireEvent.change(getInput(), { target: { value: text } });
      // Rendered in both desktop and mobile variants
      expect(
        screen.getAllByText(
          (_, node) => node?.textContent?.includes(i18n.t('chat.multiHopWarn')) ?? false
        ).length
      ).toBeGreaterThan(0);
    });

    it('shows too-long warning when exceeding DM hard limit', () => {
      renderInput({ conversationType: 'contact' });
      // DM hard limit = 156 bytes
      const text = 'x'.repeat(157);
      fireEvent.change(getInput(), { target: { value: text } });
      // Rendered in both desktop and mobile variants
      expect(
        screen.getAllByText(
          (_, node) => node?.textContent?.includes(i18n.t('chat.tooLong')) ?? false
        ).length
      ).toBeGreaterThan(0);
    });

    it('shows no warning for short messages', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      expect(screen.queryByText(i18n.t('chat.tooLong'))).toBeNull();
      expect(screen.queryByText(i18n.t('chat.multiHopWarn'))).toBeNull();
    });
  });

  describe('send blocked at hard limit', () => {
    const DM_HARD_LIMIT = 156;

    async function expectSendViaButton(value: string, shouldSend: boolean) {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value } });
      fireEvent.keyDown(getInput(), { key: 'Enter' });
      expect(onSend).not.toHaveBeenCalled();
      if (shouldSend) {
        expect(getSendButton()).toBeEnabled();
        fireEvent.click(getSendButton());
        await waitFor(() => {
          expect(onSend).toHaveBeenCalledTimes(1);
        });
      } else {
        expect(getSendButton()).toBeDisabled();
        fireEvent.click(getSendButton());
        expect(onSend).not.toHaveBeenCalled();
      }
    }

    it('disables send button when over hard limit', () => {
      renderInput({ conversationType: 'contact' });
      const text = 'x'.repeat(200); // Well over 156 byte limit
      fireEvent.change(getInput(), { target: { value: text } });
      expect(getSendButton()).toBeDisabled();
    });

    it('allows the send button at limit-1', async () => {
      await expectSendViaButton('x'.repeat(DM_HARD_LIMIT - 1), true);
    });

    it('blocks the send button at the exact hard limit', async () => {
      await expectSendViaButton('x'.repeat(DM_HARD_LIMIT), false);
    });

    it('blocks the send button past the hard limit', async () => {
      await expectSendViaButton('x'.repeat(DM_HARD_LIMIT + 1), false);
    });

    it('blocks channel send when UTF-8 accented bytes hit the name-adjusted limit', async () => {
      // "José" = 5 UTF-8 bytes (é is 2). Hard limit = 156 - 5 - 2 = 149.
      const senderName = 'José';
      const hardLimit = 156 - byteLen(senderName) - 2;
      expect(hardLimit).toBe(149);

      // "é" is 2 bytes: 74 × é = 148 (limit-1), 75 × é = 150 (over).
      // Mix with "x" to hit the exact limit: 74 × é + "x" = 149.
      const under = 'é'.repeat(74); // 148 bytes, 74 chars — a char-length check would still allow 75 é
      const atLimit = `${under}x`; // 149 bytes
      const over = 'é'.repeat(75); // 150 bytes, still only 75 chars vs 149-char hard limit

      const { unmount } = renderInput({ conversationType: 'channel', senderName });
      fireEvent.change(getInput(), { target: { value: under } });
      expect(getSendButton()).toBeEnabled();
      fireEvent.click(getSendButton());
      await waitFor(() => {
        expect(onSend).toHaveBeenCalledTimes(1);
      });
      unmount();
      onSend.mockClear();

      renderInput({ conversationType: 'channel', senderName });
      fireEvent.change(getInput(), { target: { value: atLimit } });
      expect(getSendButton()).toBeDisabled();
      fireEvent.click(getSendButton());
      expect(onSend).not.toHaveBeenCalled();

      fireEvent.change(getInput(), { target: { value: over } });
      expect(getSendButton()).toBeDisabled();
      fireEvent.click(getSendButton());
      expect(onSend).not.toHaveBeenCalled();
    });
  });

  describe('send failure toasts', () => {
    it('shows the radio no-response toast when the send outcome is unknown', async () => {
      onSend.mockRejectedValueOnce(
        new Error(
          'Send command was issued to the radio, but no response was heard back. The message may or may not have sent successfully.'
        )
      );
      renderInput({ conversationType: 'contact' });

      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      fireEvent.click(getSendButton());

      expect(await screen.findByDisplayValue('Hello')).toBeTruthy();
      expect(mockToast.error).toHaveBeenCalledWith(i18n.t('chat.radioNoConfirm'), {
        description:
          'Send command was issued to the radio, but no response was heard back. The message may or may not have sent successfully.',
      });
    });
  });

  describe('reply quote and drafts', () => {
    const channelId = 'C3B889530D4F02DB5662EA13C417F530';

    beforeEach(() => {
      localStorage.clear();
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('prefixes @[Name] and shows a visual quote via startReply', () => {
      const ref = createRef<MessageInputHandle>();
      render(
        <MessageInput
          ref={ref}
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      act(() => {
        ref.current?.startReply('Alice', 'hello world');
      });

      expect(screen.getByText(i18n.t('chat.replyTo', { sender: 'Alice' }))).toBeInTheDocument();
      expect(screen.getByText('hello world')).toBeInTheDocument();
      expect(getInput().value).toBe('@[Alice] ');
    });

    it('saves a draft keyed by getStateKey and restores it on remount', () => {
      const { unmount } = render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.change(getInput(), { target: { value: 'draft text' } });
      expect(loadConversationDraft('channel', channelId)).toBe('draft text');
      expect(localStorage.getItem(conversationDraftStorageKey('channel', channelId))).toBe(
        'draft text'
      );

      unmount();
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      expect(getInput().value).toBe('draft text');
    });

    it('deletes the draft key when the text is emptied', () => {
      saveConversationDraft('channel', channelId, 'keep me');
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.change(getInput(), { target: { value: '   ' } });
      expect(localStorage.getItem(conversationDraftStorageKey('channel', channelId))).toBeNull();
    });

    it('sends a GIF chosen from Giphy search results', async () => {
      localStorage.setItem('meshloom-giphy-api-key', 'test-key');
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          data: [
            {
              id: 'gif42',
              title: 'Party',
              images: {
                fixed_height_small: { url: 'https://media.giphy.com/media/gif42/giphy.gif' },
              },
            },
          ],
        }),
      });
      vi.stubGlobal('fetch', fetchMock);

      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      fireEvent.click(screen.getByTestId('composer-attach-tab-gif'));
      expect(await screen.findByRole('button', { name: 'Party' })).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Party' }));

      await waitFor(() => {
        expect(onSend).toHaveBeenCalledWith('g:gif42');
      });
      vi.unstubAllGlobals();
    });

    it('sends a compact g:<id> payload from a pasted Giphy URL', async () => {
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      fireEvent.click(screen.getByTestId('composer-attach-tab-gif'));
      expect(screen.getByTestId('gif-picker')).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText(i18n.t('chat.gifPaste')), {
        target: { value: 'https://giphy.com/gifs/funny-cat-abc123' },
      });
      fireEvent.click(screen.getByRole('button', { name: i18n.t('chat.gifUse') }));

      expect(await screen.findByTestId('composer-attach-trigger')).toBeInTheDocument();
      expect(onSend).toHaveBeenCalledWith('g:abc123');
      expect(screen.queryByTestId('gif-picker')).toBeNull();
    });

    it('inserts a composer emoji into the draft text', async () => {
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      expect(screen.getByTestId('emoji-picker')).toBeInTheDocument();
      expect(screen.getByLabelText(i18n.t('chat.emojiSearch'))).toBeInTheDocument();
      fireEvent.click(screen.getAllByRole('button', { name: '👍' })[0]);

      expect(getInput().value).toBe('👍');
      expect(onSend).not.toHaveBeenCalled();
    });

    it('filters composer emojis from the search field', () => {
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      fireEvent.change(screen.getByLabelText(i18n.t('chat.emojiSearch')), {
        target: { value: 'feu' },
      });

      expect(screen.getByRole('button', { name: '🔥' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '😀' })).toBeNull();
    });

    it('sends an Open location pin from radio coordinates', async () => {
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
          senderName="FR-06-PSCL-Base"
          radioLat={43.58}
          radioLon={7.12}
        />
      );

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      fireEvent.click(screen.getByTestId('composer-attach-tab-location'));
      fireEvent.click(screen.getByTestId('share-location-trigger'));

      await waitFor(() => {
        expect(onSend).toHaveBeenCalledWith('m:43.580000,7.120000|FR-06-PSCL-Base|loc');
      });
    });

    it('disables the location button without radio GPS', () => {
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      fireEvent.click(screen.getByTestId('composer-attach-tab-location'));
      expect(screen.getByTestId('share-location-trigger')).toBeDisabled();
      fireEvent.click(screen.getByTestId('share-location-trigger'));
      expect(onSend).not.toHaveBeenCalled();
      expect(mockToast.error).not.toHaveBeenCalled();
    });

    it('disables the location button when radio coordinates are 0,0', () => {
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
          radioLat={0}
          radioLon={0}
        />
      );

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      fireEvent.click(screen.getByTestId('composer-attach-tab-location'));
      expect(screen.getByTestId('share-location-trigger')).toBeDisabled();
    });

    it('prefixes a reply mention when sending a GIF', async () => {
      const ref = createRef<MessageInputHandle>();
      render(
        <MessageInput
          ref={ref}
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      act(() => {
        ref.current?.startReply('Alice', 'hello world');
      });

      fireEvent.click(screen.getByTestId('composer-attach-trigger'));
      fireEvent.click(screen.getByTestId('composer-attach-tab-gif'));
      fireEvent.change(screen.getByLabelText(i18n.t('chat.gifPaste')), {
        target: { value: 'g:xyz99' },
      });
      fireEvent.click(screen.getByRole('button', { name: i18n.t('chat.gifUse') }));

      await waitFor(() => {
        expect(onSend).toHaveBeenCalledWith('@[Alice] g:xyz99');
      });
    });

    it('clears the draft after a successful send', async () => {
      render(
        <MessageInput
          onSend={onSend}
          disabled={false}
          conversationType="channel"
          conversationId={channelId}
        />
      );

      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      fireEvent.click(getSendButton());

      expect(await screen.findByDisplayValue('')).toBeTruthy();
      expect(localStorage.getItem(conversationDraftStorageKey('channel', channelId))).toBeNull();
    });
  });
});
