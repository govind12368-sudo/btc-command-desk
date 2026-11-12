import React, { useId } from "react";

// Vantage mark: a "V" whose right stroke becomes a rising price line, on a deep navy tile.
export default function Logo({ size = 32, className = "" }) {
  // Unique gradient ids: a copy inside a hidden sidebar must not break the visible copy.
  const id = useId().replace(/:/g, "");
  const gold = `vg-gold-${id}`, tile = `vg-tile-${id}`;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={className} role="img" aria-label="Vantage BTC logo">
      <defs>
        <linearGradient id={gold} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#FCD34D" />
          <stop offset="1" stopColor="#F59E0B" />
        </linearGradient>
        <linearGradient id={tile} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#16213F" />
          <stop offset="1" stopColor="#0B132B" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="14" fill={`url(#${tile})`} stroke="#F59E0B" strokeOpacity="0.55" strokeWidth="2" />
      <path d="M15 18 L30 47 L40 28 L44 34 L51 17" fill="none" stroke={`url(#${gold})`} strokeWidth="5.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="51" cy="17" r="3.6" fill="#2DD4BF" />
    </svg>
  );
}
