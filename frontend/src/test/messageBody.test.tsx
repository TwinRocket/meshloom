import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { MessageBody } from '../components/message/MessageBody';
import { RichPayloadProvider } from '../contexts/RichPayloadContext';
import i18n from '../i18n';
import sliceEn from '../i18n/locales/slices/b.en.json';
import sliceFr from '../i18n/locales/slices/b.fr.json';

i18n.addResourceBundle('en', 'translation', sliceEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceFr, true, true);

function renderBody(
  props: Partial<React.ComponentProps<typeof MessageBody>> & { content: string },
  renderRichPayloads = true
) {
  return render(
    <RichPayloadProvider renderRichPayloads={renderRichPayloads} setRenderRichPayloads={() => {}}>
      <MessageBody {...props} />
    </RichPayloadProvider>
  );
}

describe('MessageBody', () => {
  it('renders plain text and keeps line breaks', () => {
    const { container } = renderBody({ content: 'first\nsecond' });
    expect(container.querySelector('br')).not.toBeNull();
    expect(screen.getByText('first')).toBeInTheDocument();
    expect(screen.getByText('second')).toBeInTheDocument();
  });

  it('linkifies http(s) URLs with noopener noreferrer, and only those', () => {
    renderBody({ content: 'see https://example.com/a and example.org and http://localhost/x' });
    const link = screen.getByRole('link', { name: 'https://example.com/a' });
    expect(link).toHaveAttribute('href', 'https://example.com/a');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });

  it('highlights @mentions, the radio own mention differently', () => {
    renderBody({ content: 'hi @[Bob] @[Me]', radioName: 'Me' });
    expect(screen.getByText('@[Bob]')).not.toHaveClass('font-medium');
    expect(screen.getByText('@[Me]')).toHaveClass('font-medium');
  });

  it('renders #channel references as buttons that call back, or as spans without a handler', async () => {
    const onClick = vi.fn();
    const { unmount } = renderBody({
      content: 'join #mesh-room',
      onChannelReferenceClick: onClick,
    });
    await userEvent.click(screen.getByRole('button', { name: '#mesh-room' }));
    expect(onClick).toHaveBeenCalledWith('#mesh-room');
    unmount();

    renderBody({ content: 'join #mesh-room' });
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('#mesh-room')).toBeInTheDocument();
  });

  it('renders a GIF, also when it is a reply', () => {
    const { unmount } = renderBody({ content: 'g:abc123' });
    expect(screen.getByTestId('message-gif')).toHaveAttribute(
      'src',
      'https://media.giphy.com/media/abc123/giphy.gif'
    );
    unmount();

    renderBody({ content: '@[Bob] g:abc123' });
    expect(screen.getByTestId('message-gif')).toBeInTheDocument();
    expect(screen.getByText('@[Bob]')).toBeInTheDocument();
  });

  it('renders a location pin', () => {
    renderBody({ content: 'm:43.580000,7.120000|FR-06-PSCL-Base|loc' });
    const pin = screen.getByTestId('message-location');
    expect(pin).toHaveTextContent('FR-06-PSCL-Base');
    expect(pin).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('renders Open and MeshCore One reactions', () => {
    const { unmount } = renderBody({ content: 'r:ffff:00' });
    expect(screen.getByText(i18n.t('messageList.reacted'))).toBeInTheDocument();
    unmount();

    renderBody({ content: '\u{1F44D}@[Alice]\nb45pc4ek' });
    expect(
      screen.getByText(i18n.t('messageList.reactedTo', { sender: 'Alice' }))
    ).toBeInTheDocument();
  });

  it('keeps reactions as raw text when rich payloads are off, but still renders GIFs', () => {
    const { unmount } = renderBody({ content: 'r:ffff:00' }, false);
    expect(screen.getByText('r:ffff:00')).toBeInTheDocument();
    unmount();

    renderBody({ content: 'g:abc123' }, false);
    expect(screen.getByTestId('message-gif')).toBeInTheDocument();
  });

  it('remoteMedia={false} renders a GIF as its raw text and loads no image', () => {
    const { container, unmount } = renderBody({ content: 'g:abc123', remoteMedia: false });
    expect(screen.getByText('g:abc123')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    unmount();

    const reply = renderBody({ content: '@[Bob] g:abc123', remoteMedia: false });
    expect(reply.container.querySelector('img')).toBeNull();
    expect(reply.container).toHaveTextContent('@[Bob]g:abc123');
  });

  it('remoteMedia={false} leaves other payloads alone', () => {
    renderBody({ content: 'm:43.580000,7.120000|Base|loc', remoteMedia: false });
    expect(screen.getByTestId('message-location')).toBeInTheDocument();
  });
});
