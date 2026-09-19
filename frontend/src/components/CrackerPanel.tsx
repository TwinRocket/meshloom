import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { GroupTextCracker, type ProgressReport } from 'meshcore-hashtag-cracker';
import NoSleep from 'nosleep.js';
import type { RawPacket, Channel } from '../types';
import { api } from '../api';
import { toast } from './ui/sonner';
import { cn } from '@/lib/utils';
import { getRawPackets, useRawPackets } from '../stores/rawPacketStore';
import {
  extractGroupTextFields,
  mergePriorityWordlist,
  partitionWordlistNames,
  tryHashtagCandidates,
  tryHashtagName,
} from '../utils/tryHashtagCandidates';
import { MESHCORE_CHANNEL_NAMES } from '../data/meshcoreChannels';

const EMPTY_PRIORITY_NAMES: string[] = [];

function CrackerPacketSubscription({ onPackets }: { onPackets: (packets: RawPacket[]) => void }) {
  const packets = useRawPackets();
  useEffect(() => {
    onPackets(packets);
  }, [onPackets, packets]);
  return null;
}

interface CrackedChannel {
  channelName: string;
  key: string;
  packetId: number;
  channelHash: string;
  message: string;
  crackedAt: number;
}

interface QueueItem {
  packet: RawPacket;
  identity: string;
  channelHash: string;
  historical: boolean;
  attempts: number;
  lastAttemptLength: number;
  status: 'pending' | 'cracking' | 'cracked' | 'failed';
}

export interface CrackerPanelProps {
  channels: Channel[];
  onChannelCreate: (name: string, key: string, tryHistorical: boolean) => Promise<void>;
  priorityNames?: string[];
  onHashtagDiscovered?: (name: string) => void;
  onRunningChange?: (running: boolean) => void;
  onQueueChange?: (count: number) => void;
  visible?: boolean;
}

export function CrackerPanel({
  channels,
  onChannelCreate,
  priorityNames = EMPTY_PRIORITY_NAMES,
  onHashtagDiscovered,
  onRunningChange,
  onQueueChange,
  visible = false,
}: CrackerPanelProps) {
  const { t } = useTranslation();
  const [packets, setPackets] = useState<RawPacket[]>(() => (visible ? getRawPackets() : []));
  const handlePackets = useCallback((next: RawPacket[]) => {
    setPackets(next);
  }, []);
  const [isRunning, setIsRunning] = useState(false);
  const [maxLength, setMaxLength] = useState(6);
  const [maxLengthInput, setMaxLengthInput] = useState('6');
  const [retryFailedAtNextLength, setRetryFailedAtNextLength] = useState(false);
  const [includeHistorical, setIncludeHistorical] = useState(true);
  const [includeOlder, setIncludeOlder] = useState(false);
  const [turboMode, setTurboMode] = useState(false);
  const [twoWordMode, setTwoWordMode] = useState(false);
  const [progress, setProgress] = useState<ProgressReport | null>(null);
  const [queue, setQueue] = useState<Map<string, QueueItem>>(new Map());
  const [crackedChannels, setCrackedChannels] = useState<CrackedChannel[]>([]);
  const [wordlistLoaded, setWordlistLoaded] = useState(false);
  const [gpuAvailable, setGpuAvailable] = useState<boolean | null>(null);
  const [undecryptedPacketCount, setUndecryptedPacketCount] = useState<number | null>(null);
  const [skippedDuplicates, setSkippedDuplicates] = useState(0);
  const [historicalStats, setHistoricalStats] = useState<{
    hashCount: number;
    packetCount: number;
  } | null>(null);
  const [historicalLoading, setHistoricalLoading] = useState(false);
  const [currentIdentity, setCurrentIdentity] = useState<string | null>(null);

  const crackerRef = useRef<GroupTextCracker | null>(null);
  const noSleepRef = useRef<NoSleep | null>(null);
  const isRunningRef = useRef(false);
  const abortedRef = useRef(false);
  const isProcessingRef = useRef(false);
  const queueRef = useRef<Map<string, QueueItem>>(new Map());
  const retryFailedRef = useRef(false);
  const maxLengthRef = useRef(6);
  const includeHistoricalRef = useRef(true);
  const turboModeRef = useRef(false);
  const twoWordModeRef = useRef(false);
  const seenPayloadsRef = useRef<Set<string>>(new Set());
  const adoptedChannelKeysRef = useRef<Set<string>>(new Set());
  const englishWordlistRef = useRef<string[] | null>(null);

  const priorityPartitions = useMemo(
    () => partitionWordlistNames([...MESHCORE_CHANNEL_NAMES, ...priorityNames]),
    [priorityNames]
  );
  const verbatimNamesRef = useRef(priorityPartitions.verbatim);

  useEffect(() => {
    verbatimNamesRef.current = priorityPartitions.verbatim;
  }, [priorityPartitions.verbatim]);

  // Initialize cracker and NoSleep
  useEffect(() => {
    const cracker = new GroupTextCracker();
    crackerRef.current = cracker;
    setGpuAvailable(cracker.isGpuAvailable());

    const noSleep = new NoSleep();
    noSleepRef.current = noSleep;

    return () => {
      cracker.destroy();
      crackerRef.current = null;
      noSleep.disable();
      noSleepRef.current = null;
    };
  }, []);

  // Load wordlist dynamically when panel becomes visible for the first time
  useEffect(() => {
    if (!visible || wordlistLoaded) return;

    import('meshcore-hashtag-cracker/wordlist')
      .then(({ ENGLISH_WORDLIST }) => {
        if (crackerRef.current) {
          englishWordlistRef.current = ENGLISH_WORDLIST;
          let wordlist = ENGLISH_WORDLIST;
          try {
            wordlist = mergePriorityWordlist(
              [...priorityPartitions.dictionary, ...MESHCORE_CHANNEL_NAMES],
              ENGLISH_WORDLIST
            );
          } catch (err) {
            console.error('Failed to merge priority channel names:', err);
          }
          crackerRef.current.setWordlist(wordlist);
          setWordlistLoaded(true);
        }
      })
      .catch((err) => {
        console.error('Failed to load wordlist:', err);
        toast.error(t('cracker.wordlistFailed'), {
          description: t('cracker.wordlistFailedDetail'),
        });
      });
  }, [visible, wordlistLoaded, priorityPartitions.dictionary, t]);

  useEffect(() => {
    const english = englishWordlistRef.current;
    if (!wordlistLoaded || !crackerRef.current || !english) return;
    try {
      crackerRef.current.setWordlist(
        mergePriorityWordlist(
          [...priorityPartitions.dictionary, ...MESHCORE_CHANNEL_NAMES],
          english
        )
      );
    } catch (err) {
      console.error('Failed to refresh priority channel names:', err);
    }
  }, [priorityPartitions.dictionary, wordlistLoaded]);

  // Fetch undecrypted packet count
  useEffect(() => {
    const fetchCount = () => {
      api
        .getUndecryptedPacketCount()
        .then(({ count }) => setUndecryptedPacketCount(count))
        .catch(() => setUndecryptedPacketCount(null));
    };
    fetchCount();
    // Refresh hourly (this is just for display; not critical to be up-to-date)
    const interval = setInterval(fetchCount, 3600000);
    return () => clearInterval(interval);
  }, []);

  // Get existing channel keys for filtering (memoized to avoid recreating on every render)
  const adoptedChannelKeys = useMemo(
    () =>
      new Set(
        channels
          .filter((channel) => channel.membership !== 'pending')
          .map((c) => c.key.toUpperCase())
      ),
    [channels]
  );

  useEffect(() => {
    adoptedChannelKeysRef.current = adoptedChannelKeys;
  }, [adoptedChannelKeys]);

  // Filter packets to only undecrypted GROUP_TEXT
  const undecryptedGroupText = useMemo(
    () => packets.filter((p) => p.payload_type === 'GROUP_TEXT' && !p.decrypted),
    [packets]
  );

  const enqueuePackets = useCallback((items: Array<{ packet: RawPacket; historical: boolean }>) => {
    let newSkipped = 0;
    setQueue((prev) => {
      const next = new Map(prev);
      let changed = false;
      for (const { packet, historical } of items) {
        const fields = extractGroupTextFields(packet.data);
        if (!fields) continue;
        const identity = `${fields.cipherMac}:${fields.ciphertext}`;
        if (seenPayloadsRef.current.has(identity)) {
          newSkipped += 1;
          continue;
        }
        seenPayloadsRef.current.add(identity);
        next.set(identity, {
          packet,
          identity,
          channelHash: fields.channelHash,
          historical,
          attempts: 0,
          lastAttemptLength: 0,
          status: 'pending',
        });
        changed = true;
      }
      if (!changed) return prev;
      queueRef.current = next;
      return next;
    });
    if (newSkipped > 0) {
      setSkippedDuplicates((prev) => prev + newSkipped);
    }
  }, []);

  // Update queue when packets change (deduplicated by cipher MAC + ciphertext)
  useEffect(() => {
    enqueuePackets(undecryptedGroupText.map((packet) => ({ packet, historical: false })));
  }, [enqueuePackets, undecryptedGroupText]);

  // Keep refs in sync with state
  useEffect(() => {
    queueRef.current = queue;
  }, [queue]);

  useEffect(() => {
    retryFailedRef.current = retryFailedAtNextLength;
  }, [retryFailedAtNextLength]);

  useEffect(() => {
    maxLengthRef.current = maxLength;
  }, [maxLength]);

  useEffect(() => {
    setMaxLengthInput(String(maxLength));
  }, [maxLength]);

  useEffect(() => {
    includeHistoricalRef.current = includeHistorical;
  }, [includeHistorical]);

  useEffect(() => {
    turboModeRef.current = turboMode;
  }, [turboMode]);

  useEffect(() => {
    twoWordModeRef.current = twoWordMode;
  }, [twoWordMode]);

  // Notify parent of running state changes
  useEffect(() => {
    onRunningChange?.(isRunning);
  }, [isRunning, onRunningChange]);

  // Stats (cracking count is implicit - if progress is shown, we're cracking one)
  const pendingCount = Array.from(queue.values()).filter((q) => q.status === 'pending').length;
  const failedCount = Array.from(queue.values()).filter((q) => q.status === 'failed').length;

  useEffect(() => {
    onQueueChange?.(queue.size);
  }, [onQueueChange, queue.size]);

  // Process next packet in queue
  const processNext = useCallback(async () => {
    // Prevent concurrent processing
    if (isProcessingRef.current) return;
    if (!crackerRef.current || !isRunningRef.current) return;

    const currentQueue = queueRef.current;

    // Find next pending packet
    let nextItem: QueueItem | null = null;
    let nextIdentity: string | null = null;

    for (const [identity, item] of currentQueue.entries()) {
      if (item.status === 'pending') {
        nextItem = item;
        nextIdentity = identity;
        break;
      }
    }

    // If no pending and retry option is enabled, pick the failed one with lowest lastAttemptLength
    if (!nextItem && retryFailedRef.current) {
      const failedItems = Array.from(currentQueue.entries()).filter(
        ([, item]) => item.status === 'failed' && item.lastAttemptLength < 10 // Hard cap at length 10
      );
      if (failedItems.length > 0) {
        // Sort by lastAttemptLength ascending and pick the first (lowest)
        failedItems.sort((a, b) => a[1].lastAttemptLength - b[1].lastAttemptLength);
        [nextIdentity, nextItem] = failedItems[0];
      }
    }

    if (!nextItem || nextIdentity === null) {
      // Nothing to process right now, but keep running and check again later
      if (isRunningRef.current) {
        setTimeout(() => processNext(), 1000);
      }
      return;
    }

    // Lock processing
    isProcessingRef.current = true;
    setCurrentIdentity(nextIdentity);
    setQueue((prev) => {
      const updated = new Map(prev);
      const item = updated.get(nextIdentity!);
      if (item) updated.set(nextIdentity!, { ...item, status: 'cracking' });
      return updated;
    });

    const currentMaxLength = maxLengthRef.current;
    const isRetry = nextItem.lastAttemptLength > 0;
    const targetLength = isRetry ? nextItem.lastAttemptLength + 1 : currentMaxLength;
    const validSeconds = nextItem.historical && includeOlder ? 3650 * 86400 : 30 * 86400;

    try {
      const candidate = tryHashtagCandidates(nextItem.packet.data, verbatimNamesRef.current, {
        validSeconds,
      });
      const result = candidate
        ? {
            found: true,
            roomName: candidate.roomName,
            key: candidate.key,
            decryptedMessage: candidate.message,
          }
        : await crackerRef.current.crack(
            nextItem.packet.data,
            {
              maxLength: targetLength,
              useSenderFilter: true,
              useTimestampFilter: true,
              useUtf8Filter: true,
              validSeconds,
              useTwoWordCombinations: twoWordModeRef.current,
              ...(turboModeRef.current && { gpuDispatchMs: 10000 }),
              // For retries, skip dictionary and shorter lengths - we already checked those
              ...(isRetry && {
                useDictionary: false,
                useTwoWordCombinations: false,
                startingLength: targetLength,
              }),
            },
            (prog) => {
              setProgress(prog);
            }
          );

      if (abortedRef.current) {
        abortedRef.current = false;
        isProcessingRef.current = false;
        setProgress(null);
        setCurrentIdentity(null);
        setQueue((prev) => {
          const updated = new Map(prev);
          const item = updated.get(nextIdentity!);
          if (item) updated.set(nextIdentity!, { ...item, status: 'pending' });
          return updated;
        });
        return;
      }

      if (result.found && result.roomName && result.key) {
        // Success!
        setQueue((prev) => {
          const updated = new Map(prev);
          const item = updated.get(nextIdentity!);
          if (item) {
            updated.set(nextIdentity!, {
              ...item,
              status: 'cracked',
              attempts: item.attempts + 1,
              lastAttemptLength: targetLength,
            });
          }
          return updated;
        });

        const newCracked: CrackedChannel = {
          channelName: result.roomName,
          key: result.key,
          packetId: nextItem.packet.id,
          channelHash: nextItem.channelHash,
          message: result.decryptedMessage || '',
          crackedAt: Date.now(),
        };
        setCrackedChannels((prev) => [...prev, newCracked]);

        // Auto-add channel if not already exists
        const keyUpper = result.key.toUpperCase();
        if (!adoptedChannelKeysRef.current.has(keyUpper)) {
          try {
            const channelName = '#' + result.roomName;
            await onChannelCreate(channelName, result.key, includeHistoricalRef.current);
          } catch (err) {
            console.error('Failed to create channel:', err);
            toast.error(t('cracker.saveFailed'), {
              description: err instanceof Error ? err.message : t('cracker.saveFailedDetail'),
            });
            throw err;
          }
        }

        onHashtagDiscovered?.(result.roomName);
        setQueue((prev) => {
          const updated = new Map(prev);
          for (const [identity, item] of updated) {
            if (
              tryHashtagName(item.packet.data, result.roomName!, {
                useTimestampFilter: false,
              })
            ) {
              updated.delete(identity);
            }
          }
          return updated;
        });
      } else {
        // Failed
        setQueue((prev) => {
          const updated = new Map(prev);
          const item = updated.get(nextIdentity!);
          if (item) {
            updated.set(nextIdentity!, {
              ...item,
              status: 'failed',
              attempts: item.attempts + 1,
              lastAttemptLength: targetLength,
            });
          }
          return updated;
        });
      }
    } catch (err) {
      console.error('Cracking error:', err);
      setQueue((prev) => {
        const updated = new Map(prev);
        const item = updated.get(nextIdentity!);
        if (item) {
          updated.set(nextIdentity!, {
            ...item,
            status: 'failed',
            attempts: item.attempts + 1,
            lastAttemptLength: targetLength,
          });
        }
        return updated;
      });
    }

    // Unlock processing
    isProcessingRef.current = false;
    setProgress(null);
    setCurrentIdentity(null);

    // Continue processing if still running
    if (isRunningRef.current) {
      setTimeout(() => processNext(), 100);
    }
  }, [includeOlder, onChannelCreate, onHashtagDiscovered, t]);

  // Start/stop handlers
  const handleStart = async () => {
    if (!gpuAvailable) {
      toast.error(t('cracker.webgpuTitle'), {
        description:
          typeof window !== 'undefined' && !window.isSecureContext
            ? t('cracker.httpsToast')
            : t('cracker.chromeRequired'),
      });
      return;
    }
    if (includeHistorical) {
      setHistoricalLoading(true);
      try {
        const response = await api.getGroupTextSamples(includeOlder ? 3650 : 30);
        setHistoricalStats({
          hashCount: response.hash_count,
          packetCount: response.packet_count,
        });
        enqueuePackets(
          response.samples.map((sample) => ({
            historical: true,
            packet: {
              id: sample.packet_id,
              timestamp: sample.timestamp,
              data: sample.data,
              payload_type: 'GROUP_TEXT',
              snr: null,
              rssi: null,
              decrypted: false,
              decrypted_info: null,
            },
          }))
        );
      } catch (err) {
        console.error('Failed to load historical GroupText samples:', err);
        toast.error(t('cracker.samplesFailed'));
      } finally {
        setHistoricalLoading(false);
      }
    }
    setIsRunning(true);
    isRunningRef.current = true;
    abortedRef.current = false;
    noSleepRef.current?.enable();
    setTimeout(() => processNext(), 0);
  };

  const handleStop = () => {
    setIsRunning(false);
    isRunningRef.current = false;
    abortedRef.current = true;
    crackerRef.current?.abort();
    noSleepRef.current?.disable();
  };

  const currentItem = currentIdentity ? queue.get(currentIdentity) : null;
  const currentHashItems = currentItem
    ? Array.from(queue.values()).filter((item) => item.channelHash === currentItem.channelHash)
    : [];
  const currentSampleIndex = currentItem
    ? Math.max(1, currentHashItems.findIndex((item) => item.identity === currentItem.identity) + 1)
    : 0;
  const hashGroups = Array.from(
    Array.from(queue.values()).reduce((groups, item) => {
      const group = groups.get(item.channelHash) ?? [];
      group.push(item);
      groups.set(item.channelHash, group);
      return groups;
    }, new Map<string, QueueItem[]>())
  );
  const historicalAvailable = (historicalStats?.packetCount ?? 0) > 0;

  return (
    <div className="flex flex-col h-full p-3 gap-3 bg-background border-t border-border overflow-auto">
      {visible && <CrackerPacketSubscription onPackets={handlePackets} />}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <label htmlFor="cracker-max-length" className="text-sm text-muted-foreground">
            {t('cracker.maxLength')}
          </label>
          <input
            id="cracker-max-length"
            type="number"
            min={1}
            max={10}
            value={maxLengthInput}
            onChange={(e) => {
              const nextValue = e.target.value;
              setMaxLengthInput(nextValue);
              if (nextValue === '') return;
              const parsed = Number.parseInt(nextValue, 10);
              if (Number.isNaN(parsed)) return;
              setMaxLength(Math.min(10, Math.max(1, parsed)));
            }}
            onBlur={() => {
              const parsed = Number.parseInt(maxLengthInput, 10);
              const nextValue = Number.isNaN(parsed)
                ? maxLength
                : Math.min(10, Math.max(1, parsed));
              setMaxLengthInput(String(nextValue));
              if (nextValue !== maxLength) {
                setMaxLength(nextValue);
              }
            }}
            className="w-14 px-2 py-1 text-sm bg-muted border border-border rounded"
          />
        </div>

        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={retryFailedAtNextLength}
            onChange={(e) => setRetryFailedAtNextLength(e.target.checked)}
            className="rounded"
          />
          {t('cracker.retryFailed')}
        </label>

        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={includeHistorical}
            onChange={(e) => setIncludeHistorical(e.target.checked)}
            className="rounded"
          />
          {t('cracker.decryptHistorical')}
        </label>
        {includeHistorical && (
          <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={includeOlder}
              onChange={(e) => setIncludeOlder(e.target.checked)}
              className="rounded"
            />
            {t('cracker.includeOlder')}
          </label>
        )}
        {includeHistorical && (
          <span className="text-xs text-muted-foreground">
            {undecryptedPacketCount !== null && undecryptedPacketCount > 0
              ? t('cracker.historicalCount', {
                  count: undecryptedPacketCount.toLocaleString(),
                })
              : t('cracker.historicalPlain')}
          </span>
        )}

        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={twoWordMode}
            onChange={(e) => setTwoWordMode(e.target.checked)}
            className="rounded"
          />
          {t('cracker.wordPairs')}
        </label>

        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={turboMode}
            onChange={(e) => setTurboMode(e.target.checked)}
            className="rounded"
          />
          {t('cracker.turbo')}
        </label>
      </div>

      <button
        onClick={isRunning ? handleStop : () => void handleStart()}
        disabled={!wordlistLoaded || gpuAvailable === false || historicalLoading}
        className={cn(
          'w-48 px-4 py-1.5 rounded text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
          isRunning
            ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90'
            : 'bg-primary text-primary-foreground hover:bg-primary/90',
          'disabled:opacity-50 disabled:cursor-not-allowed'
        )}
      >
        {isRunning
          ? t('cracker.stop')
          : gpuAvailable === false
            ? t('cracker.gpuUnavailable')
            : !wordlistLoaded
              ? t('cracker.loadingDict')
              : historicalLoading
                ? t('cracker.loadingSamples')
                : t('cracker.findChannels')}
      </button>

      <div className="rounded-md border border-border bg-muted/20 p-2.5 space-y-2">
        {isRunning && pendingCount === 0 && !currentItem ? (
          <div className="flex items-center gap-2 text-[0.8125rem]" role="status">
            <span className="h-2 w-2 rounded-full bg-primary animate-pulse" aria-hidden="true" />
            <span>{t('cracker.waitingTraffic')}</span>
          </div>
        ) : currentItem ? (
          <div className="space-y-1.5" role="status">
            <div className="flex justify-between gap-3 text-[0.6875rem] text-muted-foreground">
              <span>
                {progress
                  ? progress.phase === 'wordlist'
                    ? t('cracker.phaseWordlist')
                    : progress.phase === 'wordlist-pairs'
                      ? t('cracker.phasePairs')
                      : progress.phase === 'bruteforce'
                        ? t('cracker.bruteforceLength', { length: progress.currentLength })
                        : t('cracker.phasePubkey')
                  : t('cracker.phaseCandidates')}{' '}
                · {t('cracker.hashByte', { hash: currentItem.channelHash })} ·{' '}
                {t('cracker.sampleProgress', {
                  current: currentSampleIndex,
                  total: currentHashItems.length,
                })}
              </span>
              {progress && (
                <span>
                  {progress.rateKeysPerSec >= 1e9
                    ? `${(progress.rateKeysPerSec / 1e9).toFixed(2)} Gkeys/s`
                    : `${(progress.rateKeysPerSec / 1e6).toFixed(1)} Mkeys/s`}{' '}
                  · {t('cracker.eta')}{' '}
                  {progress.etaSeconds < 60
                    ? `${Math.round(progress.etaSeconds)}s`
                    : `${Math.round(progress.etaSeconds / 60)}m`}
                </span>
              )}
              {!progress && <span>— · {t('cracker.eta')} —</span>}
            </div>
            <div
              className="h-2 bg-muted rounded overflow-hidden"
              role="progressbar"
              aria-valuenow={Math.round(progress?.percent ?? 0)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={t('cracker.progressAria')}
            >
              <div
                className="h-full bg-primary transition-all duration-200"
                style={{ width: `${progress?.percent ?? 0}%` }}
              />
            </div>
          </div>
        ) : (
          <p className="text-[0.8125rem] text-muted-foreground" role="status">
            {historicalAvailable
              ? t('cracker.idleHistorical', {
                  hashes: historicalStats?.hashCount ?? 0,
                  packets: historicalStats?.packetCount ?? 0,
                  samples: queue.size,
                })
              : gpuAvailable === false
                ? t('cracker.idleNoGpu')
                : !wordlistLoaded
                  ? t('cracker.idleNoWordlist')
                  : t('cracker.idleNoGroupText')}
          </p>
        )}

        {(hashGroups.length > 0 || crackedChannels.length > 0) && (
          <div className="flex flex-wrap gap-1.5" aria-label={t('cracker.hashQueue')}>
            {hashGroups.map(([hash, items]) => {
              const status = items.some((item) => item.status === 'cracking')
                ? 'cracking'
                : items.some((item) => item.status === 'pending')
                  ? 'pending'
                  : 'failed';
              return (
                <span
                  key={hash}
                  className={cn(
                    'text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded border',
                    status === 'cracking'
                      ? 'bg-primary/10 border-primary/30 text-primary'
                      : status === 'failed'
                        ? 'bg-destructive/10 border-destructive/30 text-destructive'
                        : 'bg-muted border-border text-muted-foreground'
                  )}
                >
                  {hash} · {items.length} ·{' '}
                  {t(`cracker.chip${status[0].toUpperCase()}${status.slice(1)}`)}
                </span>
              );
            })}
            {crackedChannels.map((channel) => (
              <span
                key={`${channel.channelHash}:${channel.key}`}
                className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded border bg-success/10 border-success/30 text-success"
              >
                {channel.channelHash} · 1 · {t('cracker.chipFound', { name: channel.channelName })}
              </span>
            ))}
          </div>
        )}

        <div className="flex gap-3 text-[0.6875rem] text-muted-foreground">
          <span>
            {t('cracker.pending')} <span>{pendingCount}</span>
          </span>
          <span>
            {t('cracker.found')} <span>{crackedChannels.length}</span>
          </span>
          <span>
            {t('cracker.failed')} <span>{failedCount}</span>
          </span>
          {skippedDuplicates > 0 && (
            <span>
              {t('cracker.skipped')} {skippedDuplicates}
            </span>
          )}
        </div>
      </div>

      {/* GPU status */}
      {gpuAvailable === false && (
        <div className="text-sm text-destructive space-y-1.5" role="alert">
          <p>{t('cracker.webgpuUnavailable')}</p>
          {typeof window !== 'undefined' && !window.isSecureContext ? (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2.5 text-xs text-destructive/90">
              <p className="font-medium mb-1">{t('cracker.httpsRequired')}</p>
              <p>{t('cracker.httpsEnable')}</p>
              <ul className="list-disc ml-4 mt-1 space-y-0.5">
                <li>{t('cracker.httpsCert')}</li>
                <li>{t('cracker.httpsFlag')}</li>
              </ul>
            </div>
          ) : (
            <p>{t('cracker.chromeRequired')}</p>
          )}
        </div>
      )}
      {!wordlistLoaded && gpuAvailable !== false && (
        <div className="text-sm text-muted-foreground" role="status">
          {t('cracker.loadingWordlist')}
        </div>
      )}

      {/* Found channels list */}
      {crackedChannels.length > 0 && (
        <div>
          <div className="text-xs text-muted-foreground mb-1">{t('cracker.foundChannels')}</div>
          <div className="space-y-1">
            {crackedChannels.map((channel, i) => (
              <div
                key={i}
                className="text-sm bg-success/10 border border-success/20 rounded px-2 py-1"
              >
                <span className="text-success font-medium">#{channel.channelName}</span>
                <span className="text-muted-foreground ml-2 text-xs">
                  "{channel.message.slice(0, 50)}
                  {channel.message.length > 50 ? '...' : ''}"
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <hr className="border-border" />
      <p className="text-sm text-muted-foreground leading-relaxed">
        <Trans i18nKey="cracker.help" components={{ strong: <strong /> }} />
      </p>
    </div>
  );
}
