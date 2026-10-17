import { useCallback, useMemo, useState } from 'react';

type ServerLoginKind = 'repeater' | 'room';

type StoredPassword = {
  password: string;
};

const STORAGE_KEY_PREFIX = 'meshloom-server-password';
const inMemoryPasswords = new Map<string, StoredPassword>();

function getStorageKey(kind: ServerLoginKind, publicKey: string): string {
  return `${STORAGE_KEY_PREFIX}:${kind}:${publicKey}`;
}

function loadStoredPassword(kind: ServerLoginKind, publicKey: string): StoredPassword | null {
  try {
    const raw = localStorage.getItem(getStorageKey(kind, publicKey));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredPassword>;
    if (typeof parsed.password !== 'string' || parsed.password.length === 0) {
      return null;
    }
    return { password: parsed.password };
  } catch {
    return null;
  }
}

function loadInitialState(
  kind: ServerLoginKind,
  publicKey: string
): { password: string; rememberPassword: boolean } {
  const stored = loadStoredPassword(kind, publicKey);
  if (stored) return { password: stored.password, rememberPassword: true };
  const inMemoryStored = inMemoryPasswords.get(getStorageKey(kind, publicKey));
  if (inMemoryStored) return { password: inMemoryStored.password, rememberPassword: false };
  return { password: '', rememberPassword: false };
}

export function resetRememberedServerPasswordsForTests() {
  inMemoryPasswords.clear();
}

export function useRememberedServerPassword(kind: ServerLoginKind, publicKey: string) {
  const storageKey = useMemo(() => getStorageKey(kind, publicKey), [kind, publicKey]);
  const [initial] = useState(() => loadInitialState(kind, publicKey));
  const [password, setPassword] = useState(initial.password);
  const [rememberPassword, setRememberPassword] = useState(initial.rememberPassword);
  // Mirrors what was loaded from storage. Unlike `password` it never changes as
  // the user types, so callers can key one-shot effects (auto-login) off it.
  const [storedPassword, setStoredPassword] = useState(initial.password);

  // Reload during render when the target changes (React "adjust state on prop
  // change" pattern) instead of a synchronous setState in an effect.
  const [loadedKey, setLoadedKey] = useState(storageKey);
  if (loadedKey !== storageKey) {
    setLoadedKey(storageKey);
    const next = loadInitialState(kind, publicKey);
    setPassword(next.password);
    setStoredPassword(next.password);
    setRememberPassword(next.rememberPassword);
  }

  const persistAfterLogin = useCallback(
    (submittedPassword: string) => {
      const trimmedPassword = submittedPassword.trim();
      if (!trimmedPassword) {
        return;
      }

      inMemoryPasswords.set(storageKey, { password: trimmedPassword });

      if (!rememberPassword) {
        try {
          localStorage.removeItem(storageKey);
        } catch {
          // localStorage may be unavailable
        }
      } else {
        try {
          localStorage.setItem(storageKey, JSON.stringify({ password: trimmedPassword }));
        } catch {
          // localStorage may be unavailable
        }
      }

      setPassword(trimmedPassword);
    },
    [rememberPassword, storageKey]
  );

  const forgetPassword = useCallback(() => {
    inMemoryPasswords.delete(storageKey);
    try {
      localStorage.removeItem(storageKey);
    } catch {
      // localStorage may be unavailable
    }
    setPassword('');
    setStoredPassword('');
    setRememberPassword(false);
  }, [storageKey]);

  return {
    password,
    storedPassword,
    setPassword,
    rememberPassword,
    setRememberPassword,
    persistAfterLogin,
    forgetPassword,
  };
}
