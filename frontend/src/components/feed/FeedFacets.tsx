import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ListFilter } from 'lucide-react';

import { Button } from '../ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '../ui/sheet';
import { formatNumber } from '../../utils/formatNumber';

/** One conversation of the loaded window, with its message count. */
export interface FeedFacetItem {
  /** `conversationFacetId` */
  id: string;
  label: string;
  count: number;
}

interface FeedFacetsProps {
  channels: FeedFacetItem[];
  contacts: FeedFacetItem[];
  selected: string[];
  onChange: (selected: string[]) => void;
}

function Section({
  title,
  items,
  selected,
  onToggle,
}: {
  title: string;
  items: FeedFacetItem[];
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
}) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  return (
    <fieldset className="px-3 py-2">
      <legend className="px-1 text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </legend>
      <div className="mt-1 space-y-0.5">
        {items.map((item) => (
          <div key={item.id}>
            <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted">
              <input
                type="checkbox"
                className="rounded"
                data-testid="feed-facet-item"
                checked={selected.has(item.id)}
                onChange={() => onToggle(item.id)}
              />
              <span className="bidi-isolate min-w-0 flex-1 truncate">{item.label}</span>
              <span className="shrink-0 text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                {t('feed.facets.messageCount', {
                  count: item.count,
                  formatted: formatNumber(item.count),
                })}
              </span>
            </label>
          </div>
        ))}
      </div>
    </fieldset>
  );
}

function FacetBody({ channels, contacts, selected, onChange }: FeedFacetsProps) {
  const { t } = useTranslation();
  const picked = new Set(selected);
  const toggle = (id: string) =>
    onChange(picked.has(id) ? selected.filter((s) => s !== id) : [...selected, id]);
  return (
    <>
      <div className="flex items-center justify-between gap-2 px-4 pt-2">
        <p className="text-xs text-muted-foreground">{t('feed.facets.scope')}</p>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid="feed-facets-reset"
          disabled={selected.length === 0}
          onClick={() => onChange([])}
        >
          {t('feed.facets.reset')}
        </Button>
      </div>
      <Section
        title={t('feed.facets.channels')}
        items={channels}
        selected={picked}
        onToggle={toggle}
      />
      <Section
        title={t('feed.facets.contacts')}
        items={contacts}
        selected={picked}
        onToggle={toggle}
      />
    </>
  );
}

/**
 * The Channels and Contacts facets: the conversations present in the loaded rows with their
 * counts (client-side, on the window the feed holds, which the label says), multi-select. A
 * column on the right from `md`, a "Filters (n)" button opening a sheet on a phone.
 */
export function FeedFacets(props: FeedFacetsProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const active = props.selected.length;
  return (
    <>
      <aside
        aria-label={t('feed.facets.title')}
        className="hidden shrink-0 overflow-y-auto border-l border-border pb-2 md:order-last md:block md:w-64"
      >
        <FacetBody {...props} />
      </aside>
      <div className="flex items-center gap-2 border-b border-border px-4 py-1.5 md:hidden">
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid="feed-facets-open"
          onClick={() => setOpen(true)}
        >
          <ListFilter className="h-4 w-4" aria-hidden="true" />
          {active > 0
            ? t('feed.facets.filtersActive', { count: active, formatted: formatNumber(active) })
            : t('feed.facets.filters')}
        </Button>
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="right" className="flex w-full flex-col p-0 sm:max-w-[400px]">
            <SheetHeader className="border-b border-border px-4 py-3 pr-14 text-left">
              <SheetTitle>{t('feed.facets.title')}</SheetTitle>
              <SheetDescription className="sr-only">
                {t('feed.facets.description')}
              </SheetDescription>
            </SheetHeader>
            <div className="min-h-0 flex-1 overflow-y-auto pb-2">
              <FacetBody {...props} />
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </>
  );
}
