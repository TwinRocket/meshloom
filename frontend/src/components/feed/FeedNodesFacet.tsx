import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ListFilter } from 'lucide-react';

import { cn } from '@/lib/utils';
import { Button } from '../ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '../ui/sheet';
import type { FeedNodeSection } from './feedControl';

interface FeedNodesFacetProps {
  sections: FeedNodeSection[];
  selected: string | null;
  onSelect: (id: string | null) => void;
}

const SECTION_TITLE_KEY = {
  repeater: 'controlJournal.sectionRepeaters',
  channel: 'controlJournal.sectionChannels',
  us: 'controlJournal.sectionUs',
  unresolved: 'controlJournal.sectionUnresolved',
} as const;

function NodeList({ sections, selected, onSelect }: FeedNodesFacetProps) {
  const { t } = useTranslation();
  const itemClass = (active: boolean) =>
    cn(
      'flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm',
      active ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-muted'
    );
  return (
    <>
      <div className="px-3 pt-2">
        <button
          type="button"
          aria-pressed={selected === null}
          data-testid="feed-node-all"
          onClick={() => onSelect(null)}
          className={itemClass(selected === null)}
        >
          {t('feed.nodes.all')}
        </button>
      </div>
      {sections.map((section) => (
        <div key={section.kind} className="px-3 py-2">
          <h3 className="px-1 text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
            {t(SECTION_TITLE_KEY[section.kind])}
          </h3>
          <ul className="mt-1 space-y-0.5">
            {section.nodes.map((node) => (
              <li key={node.id}>
                <button
                  type="button"
                  aria-pressed={node.id === selected}
                  data-testid={`feed-node-${node.kind}`}
                  onClick={() => onSelect(node.id)}
                  className={itemClass(node.id === selected)}
                >
                  <span className="bidi-isolate min-w-0 truncate">{node.label}</span>
                  <span className="ml-2 shrink-0 text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                    {t('controlJournal.packetCount', { count: node.count })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  );
}

/**
 * The Nodes facet: the control threads (repeaters and rooms, channels, us, unresolved)
 * with their counts. A column from `md`, a button opening a sheet on a phone.
 */
export function FeedNodesFacet(props: FeedNodesFacetProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <nav
        aria-label={t('feed.nodes.title')}
        className="hidden shrink-0 overflow-y-auto border-r border-border md:block md:w-64"
      >
        <NodeList {...props} />
      </nav>
      <div className="border-b border-border px-4 py-1.5 md:hidden">
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid="feed-nodes-open"
          onClick={() => setOpen(true)}
        >
          <ListFilter className="h-4 w-4" aria-hidden="true" />
          {t('feed.nodes.title')}
        </Button>
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="flex w-full flex-col p-0 sm:max-w-[400px]">
            <SheetHeader className="border-b border-border px-4 py-3 pr-14 text-left">
              <SheetTitle>{t('feed.nodes.title')}</SheetTitle>
              <SheetDescription className="sr-only">{t('feed.nodes.description')}</SheetDescription>
            </SheetHeader>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <NodeList
                {...props}
                onSelect={(id) => {
                  props.onSelect(id);
                  setOpen(false);
                }}
              />
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </>
  );
}
