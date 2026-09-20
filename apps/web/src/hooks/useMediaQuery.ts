import { useEffect, useState } from "react";

/** Tracks a CSS media query in JS — needed only when a breakpoint changes
 * which component tree renders (e.g. the Calendar's genuinely distinct
 * mobile day-navigation composition vs. the desktop/tablet month grid,
 * ADR-0036), not for ordinary responsive layout, which stays pure CSS
 * throughout the rest of the app. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia(query).matches : false,
  );

  useEffect(() => {
    const mediaQueryList = window.matchMedia(query);
    const handleChange = () => setMatches(mediaQueryList.matches);
    handleChange();
    mediaQueryList.addEventListener("change", handleChange);
    return () => mediaQueryList.removeEventListener("change", handleChange);
  }, [query]);

  return matches;
}
