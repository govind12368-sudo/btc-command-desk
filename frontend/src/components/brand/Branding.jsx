import React from "react";
import { Link } from "react-router-dom";
import { GraduationCap } from "lucide-react";

export const DISCLAIMER =
  "This website and mobile application have been developed strictly for educational, learning, research, and knowledge-sharing purposes only. This platform is not intended for sale or commercial use. We do not charge any registration fees, subscription fees, or other fees for accessing or using this platform. All features and educational resources are provided completely free of charge.";

export const CREDIT = "Designed & Developed by Govind Bhendegawe";

export function DevCredit({ className = "", light = false }) {
  return (
    <span data-testid="dev-credit" className={`dev-credit whitespace-nowrap ${light ? "text-slate-600" : "text-slate-300"} ${className}`}>
      Designed &amp; Developed by <span className={light ? "text-amber-600" : "text-amber-300"}>Govind Bhendegawe</span>
    </span>
  );
}

export function FreeBadge({ light = false }) {
  return (
    <div data-testid="free-badge" className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11.5px] font-semibold ${light ? "border-teal-600/40 bg-teal-50 text-teal-700" : "border-teal-400/40 bg-teal-950/50 text-teal-300"}`}>
      <GraduationCap size={14} /> 100% Free – For Educational Purposes Only
    </div>
  );
}

export function SiteFooter({ light = false }) {
  return (
    <footer data-testid="site-footer" className={`mt-10 border-t px-4 py-6 text-center ${light ? "border-slate-200 text-slate-600" : "border-[#1e2a4a] text-slate-400"}`}>
      <p className="mx-auto max-w-3xl text-[11.5px] leading-relaxed" data-testid="footer-disclaimer">
        <span className={`font-semibold ${light ? "text-slate-800" : "text-slate-200"}`}>Disclaimer: </span>{DISCLAIMER}
      </p>
      <div className="mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-[11.5px]">
        <Link to="/disclaimer" data-testid="footer-disclaimer-link" className={`underline-offset-4 hover:underline ${light ? "text-amber-700" : "text-amber-300"}`}>Disclaimer</Link>
        <span data-testid="footer-copyright">© 2026 | Designed &amp; Developed by Govind Bhendegawe | For Educational Purposes Only</span>
      </div>
    </footer>
  );
}
