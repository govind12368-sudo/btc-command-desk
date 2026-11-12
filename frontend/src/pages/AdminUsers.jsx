import React, { useState } from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, Chip, PageHead, Note, Stat } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { ist } from "@/lib/fmt";

export default function AdminUsers() {
  const [q, setQ] = useState("");
  const { data, reload } = useFetch(`/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`, 30000);
  const toggle = async (u) => {
    const status = u.status === "active" ? "disabled" : "active"; // pending -> active = approve
    if (status === "disabled" && !window.confirm(`Deactivate ${u.username || u.email}? They will be signed out immediately.`)) return;
    try { await api.put(`/admin/users/${u.id}/status`, { status }); toast.success(status === "active" ? (u.status === "pending" ? "Account approved" : "Account reactivated") : "Account deactivated"); reload(); } catch (x) { toast.error(errText(x)); }
  };
  const users = data?.users || [];
  return (
    <div className="space-y-4">
      <PageHead title="User Management" sub="Admin only — view registered accounts and control access" />
      {data?.registration_mode && <Note>New sign-ups: <b>{data.registration_mode === "approval" ? "need your approval" : data.registration_mode === "open" ? "open (no approval)" : "closed"}</b>. Change this in Settings → New account registration.</Note>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Registered" value={data?.total ?? "—"} testid="users-total" />
        <Stat label="Active" value={users.filter((u) => u.status === "active").length} />
        <Stat label="Waiting approval" value={data?.pending ?? users.filter((u) => u.status === "pending").length} testid="users-pending" />
        <Stat label="Deactivated" value={users.filter((u) => u.status === "disabled").length} />
        <Stat label="Verified email" value={users.filter((u) => u.email_verified).length} />
      </div>
      <Card title="Accounts" testid="users-table" right={<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, username, email" data-testid="users-search" className="rounded border border-[#1e2a4a] bg-[#0f172a] px-2.5 py-1.5 text-xs" />}>
        <div className="overflow-x-auto scroll-thin"><table className="desk-table">
          <thead><tr><th>Name</th><th>Username</th><th>Email</th><th>Role</th><th>Verified</th><th>Status</th><th>Joined</th><th>Last login</th><th></th></tr></thead>
          <tbody>{users.map((u) => (
            <tr key={u.id} data-testid={`user-row-${u.username || u.id}`}>
              <td>{u.name}</td><td className="font-mono">{u.username}</td><td className="font-mono text-xs">{u.email}</td>
              <td><Chip status={u.role === "admin" ? "REVIEW" : "PASS"} label={u.role.toUpperCase()} /></td>
              <td><Chip status={u.email_verified ? "PASS" : "WAIT"} label={u.email_verified ? "VERIFIED" : "PENDING"} /></td>
              <td><Chip status={u.status === "active" ? "PASS" : u.status === "pending" ? "WAIT" : "BLOCKED"} label={u.status.toUpperCase()} testid={`user-status-${u.username || u.id}`} /></td>
              <td className="font-mono text-[11px]">{ist(u.created_at)}</td><td className="font-mono text-[11px]">{ist(u.last_login)}</td>
              <td>{u.role !== "admin" && <Btn tone={u.status === "active" ? "red" : "teal"} data-testid={`user-toggle-${u.username || u.id}`} onClick={() => toggle(u)}>{u.status === "active" ? "Deactivate" : u.status === "pending" ? "Approve" : "Reactivate"}</Btn>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </Card>
      <Note>{data?.note || "Passwords are stored as one-way bcrypt hashes and can never be viewed."}</Note>
    </div>
  );
}
