import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { REACTION_EMOJIS } from '../utils/meshcoreOpenPayloads';
import { filterEmojis } from '../utils/emojiSearch';

interface ComposerEmojiPickerProps {
  onSelect: (emoji: string) => void;
  onClose?: () => void;
  disabled?: boolean;
  embedded?: boolean;
}

export function ComposerEmojiPicker({
  onSelect,
  onClose,
  disabled = false,
  embedded = false,
}: ComposerEmojiPickerProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const emojis = useMemo(() => filterEmojis(REACTION_EMOJIS, query), [query]);

  return (
    <div
      data-testid="emoji-picker"
      className={
        embedded
          ? undefined
          : 'absolute inset-x-0 bottom-full z-40 mb-2 overflow-hidden rounded-xl border border-border bg-card shadow-lg'
      }
    >
      {!embedded && (
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <p className="min-w-0 flex-1 text-sm font-semibold">{t('chat.emojiPicker')}</p>
          {onClose && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 flex-shrink-0"
              aria-label={t('chat.emojiClose')}
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      )}
      <div className="space-y-2 p-3">
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('chat.emojiSearch')}
          aria-label={t('chat.emojiSearch')}
          disabled={disabled}
        />
        <div className="grid max-h-56 grid-cols-8 gap-0.5 overflow-y-auto">
          {emojis.length === 0 && (
            <p className="col-span-full py-6 text-center text-xs text-muted-foreground">
              {t('chat.emojiEmpty')}
            </p>
          )}
          {emojis.map((emoji, index) => (
            <button
              key={`${emoji}-${index}`}
              type="button"
              disabled={disabled}
              className="h-9 w-full rounded-md text-lg hover:bg-accent disabled:opacity-50"
              aria-label={emoji}
              onClick={() => onSelect(emoji)}
            >
              {emoji}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
