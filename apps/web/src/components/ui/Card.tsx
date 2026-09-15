import type { HTMLAttributes } from "react";
import { Link, type LinkProps } from "react-router-dom";

import styles from "./Card.module.css";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`${styles.card} ${className ?? ""}`} {...rest} />;
}

/** A whole-card navigation target rendered as a real `<a>` (via
 * react-router's `Link`) rather than a `<div onClick>` — keyboard-focusable
 * and announced as a link by default, no bespoke role/key handling
 * needed. Used for list rows (e.g. a Group in the Groups list) that
 * navigate to a detail page. */
export function LinkCard({ className, ...rest }: LinkProps) {
  return <Link className={`${styles.linkCard} ${className ?? ""}`} {...rest} />;
}
