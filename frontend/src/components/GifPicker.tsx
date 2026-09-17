import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { getSavedGiphyApiKey } from '../utils/giphyPreference';
import { giphyUrlForId, parseGif } from '../utils/meshcoreOpenPayloads';

interface GiphyResult {
  id: string;
  title: string;
  previewUrl: string;
}

interface GifPickerProps {
  onSelect: (gifId: string) => void;
  onClose?: () => void;
  disabled?: boolean;
  embedded?: boolean;
}

async function fetchGiphy(query: string, apiKey: string): Promise<GiphyResult[]> {
  const trimmed = query.trim();
  const endpoint = trimmed
    ? `https://api.giphy.com/v1/gifs/search?api_key=${encodeURIComponent(apiKey)}&q=${encodeURIComponent(trimmed)}&limit=24&rating=g&lang=fr`
    : `https://api.giphy.com/v1/gifs/trending?api_key=${encodeURIComponent(apiKey)}&limit=24&rating=g`;
  const response = await fetch(endpoint);
  if (!response.ok) {
    throw new Error(`giphy ${response.status}`);
  }
  const json = (await response.json()) as {
    data?: Array<{
      id: string;
      title?: string;
      images?: { fixed_height_small?: { url?: string }; downsized?: { url?: string } };
    }>;
  };
  return (json.data ?? []).flatMap((item) => {
    if (!item.id) return [];
    return [
      {
        id: item.id,
        title: item.title || item.id,
        previewUrl:
          item.images?.fixed_height_small?.url ||
          item.images?.downsized?.url ||
          giphyUrlForId(item.id),
      },
    ];
  });
}

export function GifPicker({
  onSelect,
  onClose,
  disabled = false,
  embedded = false,
}: GifPickerProps) {
  const { t } = useTranslation();
  const apiKey = useMemo(() => getSavedGiphyApiKey(), []);
  const [query, setQuery] = useState('');
  const [paste, setPaste] = useState('');
  const [results, setResults] = useState<GiphyResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const requestIdRef = useRef(0);

  const pastedId = parseGif(paste);

  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!apiKey) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }
    const requestId = ++requestIdRef.current;
    const timer = window.setTimeout(
      () => {
        setLoading(true);
        setError(null);
        void fetchGiphy(query, apiKey)
          .then((next) => {
            if (requestIdRef.current !== requestId) return;
            setResults(next);
          })
          .catch(() => {
            if (requestIdRef.current !== requestId) return;
            setResults([]);
            setError(t('chat.gifError'));
          })
          .finally(() => {
            if (requestIdRef.current !== requestId) return;
            setLoading(false);
          });
      },
      query.trim() ? 300 : 0
    );
    return () => {
      window.clearTimeout(timer);
    };
  }, [apiKey, query, t]);

  const handlePasteSend = useCallback(() => {
    if (!pastedId || disabled) return;
    onSelect(pastedId);
  }, [disabled, onSelect, pastedId]);

  return (
    <div
      data-testid="gif-picker"
      className={
        embedded
          ? undefined
          : 'absolute inset-x-0 bottom-full z-40 mb-2 overflow-hidden rounded-xl border border-border bg-card shadow-lg'
      }
    >
      {!embedded && (
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <p className="min-w-0 flex-1 text-sm font-semibold">{t('chat.gifPicker')}</p>
          {onClose && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 flex-shrink-0"
              aria-label={t('chat.gifClose')}
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      )}
      <div className="space-y-2 p-3">
        <Input
          ref={searchRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('chat.gifSearch')}
          aria-label={t('chat.gifSearch')}
          disabled={!apiKey || disabled}
        />
        <div className="flex gap-2">
          <Input
            value={paste}
            onChange={(event) => setPaste(event.target.value)}
            placeholder={t('chat.gifPaste')}
            aria-label={t('chat.gifPaste')}
            disabled={disabled}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                handlePasteSend();
              }
            }}
          />
          <Button type="button" disabled={disabled || !pastedId} onClick={handlePasteSend}>
            {t('chat.gifUse')}
          </Button>
        </div>
        {!apiKey && (
          <p className="text-[0.8125rem] text-muted-foreground">{t('chat.gifNeedKey')}</p>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
        {apiKey && (
          <div
            data-testid="gif-picker-results"
            className="grid max-h-64 grid-cols-3 gap-1.5 overflow-y-auto sm:grid-cols-4"
          >
            {loading && results.length === 0 && (
              <p className="col-span-full py-6 text-center text-xs text-muted-foreground">
                {t('chat.gifLoading')}
              </p>
            )}
            {!loading && results.length === 0 && !error && (
              <p className="col-span-full py-6 text-center text-xs text-muted-foreground">
                {t('chat.gifEmpty')}
              </p>
            )}
            {results.map((gif) => (
              <button
                key={gif.id}
                type="button"
                disabled={disabled}
                className="overflow-hidden rounded-md bg-muted/50 hover:ring-2 hover:ring-ring disabled:opacity-50"
                onClick={() => onSelect(gif.id)}
                aria-label={gif.title}
              >
                <img src={gif.previewUrl} alt={gif.title} className="h-20 w-full object-cover" />
              </button>
            ))}
          </div>
        )}
        <p className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
          {t('chat.gifAttribution')}
        </p>
      </div>
    </div>
  );
}
