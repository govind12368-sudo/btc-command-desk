import React from "react";
import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { useDesk } from "@/context/DeskContext";
import { DISCLAIMER, SiteFooter, FreeBadge, DevCredit } from "@/components/brand/Branding";

function Body() {
  return (
    <div className="mx-auto max-w-3xl" data-testid="disclaimer-page">
      <div className="flex items-center gap-2 text-amber-300"><ShieldCheck size={20} /><h1 className="text-2xl font-bold text-slate-50 sm:text-3xl">Disclaimer</h1></div>
      <div className="mt-4"><FreeBadge /></div>
      <div className="mt-6 rounded-xl border border-[#1e2a4a] bg-[#131c35]/80 p-5 sm:p-7 leading-relaxed text-slate-200">
        <p className="text-[15px]" data-testid="disclaimer-text">{DISCLAIMER}</p>
        <ul className="mt-5 list-disc space-y-2 pl-5 text-sm text-slate-400">
          <li>No payment, subscription or paid membership exists on this platform.</li>
          <li>Market data, signals, models and statistics are shown for learning and research. They are not financial advice and do not place orders.</li>
          <li>Always do your own research before any real trading decision.</li>
        </ul>
        <p className="mt-5 text-sm"><DevCredit /></p>
      </div>
    </div>
  );
}

export default function Disclaimer() {
  const { user } = useDesk();
  if (user) return <Body />;
  return (
    <div className="auth-bg min-h-screen px-4 pt-10 text-slate-100">
      <div className="mb-6 text-center"><Link to="/login" className="text-sm text-amber-300 hover:underline" data-testid="disclaimer-back">← Back to sign in</Link></div>
      <Body />
      <SiteFooter />
    </div>
  );
}
