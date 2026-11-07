import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));

import {
  AllowedOriginsSettings,
  normalizeOrigin,
} from '../components/settings/AllowedOriginsSettings';
import i18n from '../i18n';

function renderField(origins: string[] = [], onSave = vi.fn(async (_: string[]) => {})) {
  render(<AllowedOriginsSettings origins={origins} onSave={onSave} />);
  return { onSave, textarea: screen.getByRole('textbox') };
}

const saveButton = () =>
  screen.getByRole('button', { name: i18n.t('settings.allowedOrigins.save') });

describe('normalizeOrigin', () => {
  it.each([
    ['HTTPS://Dashboard.Example.org/', 'https://dashboard.example.org'],
    ['https://example.org:443', 'https://example.org'],
    ['http://192.168.1.20:8080', 'http://192.168.1.20:8080'],
    ['http://[::1]:5173', 'http://[::1]:5173'],
  ])('normalizes %s', (raw, expected) => {
    expect(normalizeOrigin(raw)).toBe(expected);
  });

  it.each([
    '*',
    'https://*.example.org',
    'example.org',
    'ftp://example.org',
    'https://example.org/path',
    'https://example.org?x=1',
    'https://example.org?',
    'https://example.org#top',
    'https://user:pass@example.org',
  ])('refuses %s', (raw) => {
    expect(normalizeOrigin(raw)).toBeNull();
  });
});

describe('AllowedOriginsSettings', () => {
  beforeEach(() => {
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
  });

  it('says an empty list is same-origin only and has nothing to save', () => {
    renderField();
    expect(screen.getByText(i18n.t('settings.allowedOrigins.sameOriginOnly'))).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it('saves the normalized, deduplicated list', async () => {
    const { onSave, textarea } = renderField();
    fireEvent.change(textarea, {
      target: {
        value: 'HTTPS://Dashboard.Example.org/\n\nhttps://dashboard.example.org\nhttp://a.lan:8080',
      },
    });
    fireEvent.click(saveButton());

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(['https://dashboard.example.org', 'http://a.lan:8080'])
    );
    expect(mocks.toast.success).toHaveBeenCalledWith(i18n.t('settings.allowedOrigins.saved'));
  });

  it('flags an invalid line and blocks the save', () => {
    const { onSave, textarea } = renderField();
    fireEvent.change(textarea, { target: { value: 'https://ok.example.org\n*' } });

    expect(screen.getByRole('alert')).toHaveTextContent('*');
    expect(textarea).toHaveAttribute('aria-invalid', 'true');
    expect(saveButton()).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('can clear a saved list back to same-origin only', async () => {
    const { onSave, textarea } = renderField(['https://dashboard.example.org']);
    expect(textarea).toHaveValue('https://dashboard.example.org');
    fireEvent.change(textarea, { target: { value: '' } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(onSave).toHaveBeenCalledWith([]));
  });

  it('reports a refused save', async () => {
    const onSave = vi.fn(async () => {
      throw new Error('422');
    });
    const { textarea } = renderField([], onSave);
    fireEvent.change(textarea, { target: { value: 'https://ok.example.org' } });
    fireEvent.click(saveButton());

    await waitFor(() =>
      expect(mocks.toast.error).toHaveBeenCalledWith(
        i18n.t('settings.allowedOrigins.saveFailed'),
        expect.anything()
      )
    );
  });
});
