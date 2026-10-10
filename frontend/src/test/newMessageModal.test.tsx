/**
 * Tests for NewMessageModal form state reset.
 *
 * Verifies that form fields are cleared when the modal closes (via Create,
 * Cancel, or Dialog dismiss) and when switching tabs.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { NewMessageModal } from '../components/NewMessageModal';
import i18n from '../i18n';
import sliceEn from '../i18n/locales/slices/b.en.json';
import sliceFr from '../i18n/locales/slices/b.fr.json';

i18n.addResourceBundle('en', 'translation', sliceEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceFr, true, true);
import { toast } from '../components/ui/sonner';

// Mock sonner (toast)
vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockToast = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

describe('NewMessageModal form reset', () => {
  const onClose = vi.fn();
  const onCreateContact = vi.fn().mockResolvedValue(undefined);
  const onCreateChannel = vi.fn().mockResolvedValue(undefined);
  const onCreateHashtagChannel = vi.fn().mockResolvedValue(undefined);
  const onBulkAddHashtagChannels = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderModal(
    open = true,
    overrides: Partial<Parameters<typeof NewMessageModal>[0]> = {}
  ) {
    return render(
      <NewMessageModal
        open={open}
        undecryptedCount={5}
        onClose={onClose}
        onCreateContact={onCreateContact}
        onCreateChannel={onCreateChannel}
        onCreateHashtagChannel={onCreateHashtagChannel}
        onBulkAddHashtagChannels={onBulkAddHashtagChannels}
        {...overrides}
      />
    );
  }

  async function switchToTab(user: ReturnType<typeof userEvent.setup>, name: string) {
    await user.click(screen.getByRole('tab', { name }));
  }

  describe('hashtag tab', () => {
    it('prefills the hashtag tab from a linked channel request', async () => {
      renderModal(true, {
        prefillRequest: {
          tab: 'hashtag',
          hashtagName: 'mesh-room',
          nonce: 1,
        },
      });

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: i18n.t('newMessage.tabHashtag') })).toHaveAttribute(
          'data-state',
          'active'
        );
      });
      expect(
        (screen.getByPlaceholderText(i18n.t('newMessage.hashtagPlaceholder')) as HTMLInputElement)
          .value
      ).toBe('mesh-room');
    });

    it('clears name after successful Create', async () => {
      const user = userEvent.setup();
      const { unmount } = renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      const input = screen.getByPlaceholderText(
        i18n.t('newMessage.hashtagPlaceholder')
      ) as HTMLInputElement;
      await user.type(input, 'testchan');
      expect(input.value).toBe('testchan');

      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      await waitFor(() => {
        expect(onCreateHashtagChannel).toHaveBeenCalledWith('#testchan', false);
      });
      expect(onClose).toHaveBeenCalled();
      unmount();

      // Re-render to simulate reopening — state should be reset
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));
      expect(
        (screen.getByPlaceholderText(i18n.t('newMessage.hashtagPlaceholder')) as HTMLInputElement)
          .value
      ).toBe('');
    });

    it('clears name when Cancel is clicked', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      const input = screen.getByPlaceholderText(
        i18n.t('newMessage.hashtagPlaceholder')
      ) as HTMLInputElement;
      await user.type(input, 'mychannel');
      expect(input.value).toBe('mychannel');

      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.cancel') }));
      expect(onClose).toHaveBeenCalled();
    });

    it('rejects extended characters when the extended toggle is off', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.hashtagPlaceholder')),
        'Cats&Dogs'
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      expect(onCreateHashtagChannel).not.toHaveBeenCalled();
      expect(screen.getByText(i18n.t('newMessage.hashtagInvalid'))).toBeTruthy();
    });

    it('normalizes the typed name like the official app', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.hashtagPlaceholder')),
        ' #Test-1 '
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      await waitFor(() => {
        expect(onCreateHashtagChannel).toHaveBeenCalledWith('#test-1', false);
      });
    });

    it('rejects a typed name over 30 bytes', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.hashtagPlaceholder')),
        'a'.repeat(30)
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      expect(onCreateHashtagChannel).not.toHaveBeenCalled();
      expect(screen.getByText(i18n.t('newMessage.hashtagTooLong'))).toBeTruthy();
    });

    it('hashes the name verbatim when the extended toggle is on', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      await user.click(
        screen.getByRole('checkbox', {
          name: i18n.t('newMessage.permitExtended'),
        })
      );
      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.hashtagPlaceholder')),
        'Cats&Dogs'
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      await waitFor(() => {
        expect(onCreateHashtagChannel).toHaveBeenCalledWith('#Cats&Dogs', false);
      });
    });
  });

  describe('bulk hashtag tab', () => {
    it('is only visible when enabled', () => {
      renderModal();
      expect(screen.queryByRole('tab', { name: i18n.t('newMessage.tabBulk') })).toBeNull();
    });

    it('opens on the bulk tab when enabled and submits normalized channel names', async () => {
      const user = userEvent.setup();
      renderModal(true, { showBulkAddChannelTab: true });

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: i18n.t('newMessage.tabBulk') })).toHaveAttribute(
          'data-state',
          'active'
        );
      });

      await user.type(
        screen.getByRole('textbox', { name: i18n.t('newMessage.bulkNamesAria') }),
        '#Ops{enter}mesh-room another-room #Ops'
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.addChannels') }));

      await waitFor(() => {
        expect(onBulkAddHashtagChannels).toHaveBeenCalledWith(
          ['#ops', '#mesh-room', '#another-room'],
          false
        );
      });
      expect(onClose).toHaveBeenCalled();
    });

    it('shows invalid bulk channel names before submitting', async () => {
      const user = userEvent.setup();
      renderModal(true, { showBulkAddChannelTab: true });

      await user.type(
        screen.getByRole('textbox', { name: i18n.t('newMessage.bulkNamesAria') }),
        'good-room bad_room'
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.addChannels') }));

      expect(onBulkAddHashtagChannels).not.toHaveBeenCalled();
      expect(
        screen.getByText(i18n.t('newMessage.invalidNames', { names: 'bad_room' }))
      ).toBeTruthy();
    });

    it('accepts extended names split by lines when the toggle is on', async () => {
      const user = userEvent.setup();
      renderModal(true, { showBulkAddChannelTab: true });

      await user.click(
        screen.getByRole('checkbox', {
          name: i18n.t('newMessage.permitExtended'),
        })
      );
      await user.type(
        screen.getByRole('textbox', { name: i18n.t('newMessage.bulkNamesAria') }),
        '#Cats & Dogs{enter}Mesh Room'
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.addChannels') }));

      await waitFor(() => {
        expect(onBulkAddHashtagChannels).toHaveBeenCalledWith(
          ['#Cats & Dogs', '#Mesh Room'],
          false
        );
      });
    });
  });

  describe('meshcore:// import', () => {
    it('maps an official contact URI onto Create → onCreateContact', async () => {
      const user = userEvent.setup();
      renderModal();
      const publicKey = '9cd8fcf22a47333b591d96a2b848b73f457b1bb1a3ea2453a885f9e5787765b1';
      fireEvent.change(screen.getByLabelText(i18n.t('share.importLabel')), {
        target: {
          value: `meshcore://contact/add?name=Example+Contact&public_key=${publicKey}&type=2`,
        },
      });

      expect(
        (screen.getByPlaceholderText(i18n.t('newMessage.namePlaceholder')) as HTMLInputElement)
          .value
      ).toBe('Example Contact');
      expect(
        (screen.getByPlaceholderText(i18n.t('newMessage.publicKeyPlaceholder')) as HTMLInputElement)
          .value
      ).toBe(publicKey);
      expect((screen.getByLabelText(i18n.t('newMessage.type')) as HTMLSelectElement).value).toBe(
        '2'
      );

      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));
      await waitFor(() => {
        expect(onCreateContact).toHaveBeenCalledWith('Example Contact', publicKey, false, 2);
      });
    });

    it('maps an official channel URI onto Create → onCreateChannel', async () => {
      const user = userEvent.setup();
      renderModal();
      const secret = '8b3387e9c5cdea6ac9e5edbaa115cd72';
      fireEvent.change(screen.getByLabelText(i18n.t('share.importLabel')), {
        target: {
          value: `meshcore://channel/add?name=Public&secret=${secret}&region_scope=%23Esperance`,
        },
      });

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: i18n.t('newMessage.tabChannel') })).toHaveAttribute(
          'data-state',
          'active'
        );
      });
      expect(
        (
          screen.getByPlaceholderText(
            i18n.t('newMessage.channelNamePlaceholder')
          ) as HTMLInputElement
        ).value
      ).toBe('Public');
      expect(
        (
          screen.getByPlaceholderText(
            i18n.t('newMessage.channelKeyPlaceholder')
          ) as HTMLInputElement
        ).value
      ).toBe(secret);

      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));
      await waitFor(() => {
        expect(onCreateChannel).toHaveBeenCalledWith('Public', secret, false);
      });
    });

    it('rejects unofficial meshcore paths', async () => {
      renderModal();
      fireEvent.change(screen.getByLabelText(i18n.t('share.importLabel')), {
        target: { value: 'meshcore://settings' },
      });
      expect(screen.getByText(i18n.t('share.invalidUri'))).toBeTruthy();
      expect(onCreateContact).not.toHaveBeenCalled();
      expect(onCreateChannel).not.toHaveBeenCalled();
    });
  });

  describe('new-contact tab', () => {
    it('clears name and key after successful Create', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabContact'));

      await user.type(screen.getByPlaceholderText(i18n.t('newMessage.namePlaceholder')), 'Bob');
      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.publicKeyPlaceholder')),
        'bb'.repeat(32)
      );

      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      await waitFor(() => {
        expect(onCreateContact).toHaveBeenCalledWith('Bob', 'bb'.repeat(32), false, 1);
      });
      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('new-channel tab', () => {
    it('clears name and key after successful Create', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabChannel'));

      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.channelNamePlaceholder')),
        'MyRoom'
      );
      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.channelKeyPlaceholder')),
        'cc'.repeat(16)
      );

      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      await waitFor(() => {
        expect(onCreateChannel).toHaveBeenCalledWith('MyRoom', 'cc'.repeat(16), false);
      });
      expect(onClose).toHaveBeenCalled();
    });

    it('toasts when creation fails', async () => {
      const user = userEvent.setup();
      onCreateChannel.mockRejectedValueOnce(new Error('Bad key'));
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabChannel'));

      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.channelNamePlaceholder')),
        'MyRoom'
      );
      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.channelKeyPlaceholder')),
        'cc'.repeat(16)
      );
      await user.click(screen.getByRole('button', { name: i18n.t('newMessage.create') }));

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith(i18n.t('newMessage.createFailed'), {
          description: 'Bad key',
        });
      });
      expect(screen.getByText('Bad key')).toBeTruthy();
    });
  });

  describe('tab switching resets form', () => {
    it('clears contact fields when switching to channel tab', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabContact'));

      await user.type(screen.getByPlaceholderText(i18n.t('newMessage.namePlaceholder')), 'Bob');
      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.publicKeyPlaceholder')),
        'deadbeef'
      );

      // Switch to Private Channel tab — fields should reset
      await switchToTab(user, i18n.t('newMessage.tabChannel'));

      expect(
        (
          screen.getByPlaceholderText(
            i18n.t('newMessage.channelNamePlaceholder')
          ) as HTMLInputElement
        ).value
      ).toBe('');
      expect(
        (
          screen.getByPlaceholderText(
            i18n.t('newMessage.channelKeyPlaceholder')
          ) as HTMLInputElement
        ).value
      ).toBe('');
    });

    it('clears channel fields when switching to hashtag tab', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabChannel'));

      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.channelNamePlaceholder')),
        'SecretRoom'
      );
      await user.type(
        screen.getByPlaceholderText(i18n.t('newMessage.channelKeyPlaceholder')),
        'ff'.repeat(16)
      );

      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      expect(
        (screen.getByPlaceholderText(i18n.t('newMessage.hashtagPlaceholder')) as HTMLInputElement)
          .value
      ).toBe('');
    });
  });

  describe('tryHistorical checkbox resets', () => {
    it('resets tryHistorical when switching tabs', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      // Check the "Try decrypting" checkbox
      const checkbox = screen.getByRole('checkbox', {
        name: i18n.t('newMessage.tryDecrypt', { count: 5 }),
      });
      await user.click(checkbox);

      // The streaming message should appear
      expect(screen.getByText(i18n.t('newMessage.tryDecryptHelp'))).toBeTruthy();

      // Switch tab and come back
      await switchToTab(user, i18n.t('newMessage.tabContact'));
      await switchToTab(user, i18n.t('newMessage.tabHashtag'));

      // The streaming message should be gone (tryHistorical was reset)
      expect(screen.queryByText(i18n.t('newMessage.tryDecryptHelp'))).toBeNull();
    });
  });
});
