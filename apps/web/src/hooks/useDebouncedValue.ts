import { useEffect, useState } from "react";

/** Delays reflecting `value` until it has stopped changing for `delayMs` —
 * used so the People search box doesn't fire a server request on every
 * keystroke (the search is a real `GET /persons?search=` query, not a
 * client-side filter over an already-fetched page). */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
