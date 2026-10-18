import { useEffect, useId } from 'react';
import { useTranslation } from 'react-i18next';
import {
  loadShareStatus,
  requestShareMeta,
  useShareMetaEntry,
  useShareStatus,
} from '../../stores/shareMetaStore';
import { displayShareHost, looksLikeShareLink, parseShareLink } from '../../utils/shareLinks';

// FSI..PDI around the remote host in plain-string contexts (title); messageText's
// isolateBidi does the same but importing it here would be a cycle.
const LINK_CLASS = 'text-primary underline hover:text-primary/80';

/**
 * An anchor for a URL of a message. A plain anchor unless the URL is a service
 * link and sharing is on; then it shows a small destination hint (`-> example.org`)
 * read from `GET /api/share/{id}/meta` on hover or focus only, never on render.
 */
export function ShareLinkAnchor({ href }: { href: string }) {
  const { t } = useTranslation();
  const candidate = looksLikeShareLink(href);
  const status = useShareStatus();
  const id = status?.enabled ? parseShareLink(href, status.hosts) : null;
  const entry = useShareMetaEntry(id);

  // The status route is local (no egress); only a URL shaped like a service link asks.
  useEffect(() => {
    if (candidate) void loadShareStatus();
  }, [candidate]);

  const warm = () => {
    if (id) void requestShareMeta(id);
  };
  // The host comes from the share service: sanitised, punycode, cut (never raw).
  const destination = displayShareHost(
    id && entry?.state === 'ready' ? entry.meta.target_host : null
  );
  const hintId = useId();

  return (
    <>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={LINK_CLASS}
        title={
          id
            ? destination
              ? t('shareLink.titleTo', { host: `\u2068${destination.full}\u2069` })
              : t('shareLink.title')
            : undefined
        }
        aria-describedby={destination ? hintId : undefined}
        onMouseEnter={id ? warm : undefined}
        onFocus={id ? warm : undefined}
      >
        {href}
      </a>
      {destination && (
        <span
          id={hintId}
          data-testid="share-link-hint"
          className="ml-1 text-xs text-muted-foreground"
          title={destination.full}
        >
          {'→ '}
          <bdi dir="ltr" className="inline-block max-w-[16rem] truncate align-bottom">
            {destination.text}
          </bdi>
        </span>
      )}
    </>
  );
}
