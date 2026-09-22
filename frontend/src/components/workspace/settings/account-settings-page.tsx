"use client";

import { LogOutIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";

import { BillingSettings } from "./billing-settings";
import { ByokSettings } from "./byok-settings";
import { CreditsMeter } from "./credits-meter";
import { ReferralCard } from "./referral-card";
import { SettingsSection } from "./settings-section";

export function AccountSettingsPage() {
  const { user, logout, refreshUser } = useAuth();
  const { t } = useI18n();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Email-change form (separate from password change).
  const [newEmail, setNewEmail] = useState("");
  const [emailPassword, setEmailPassword] = useState("");
  const [emailMessage, setEmailMessage] = useState("");
  const [emailError, setEmailError] = useState("");
  const [emailLoading, setEmailLoading] = useState(false);

  const handleChangeEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    setEmailError("");
    setEmailMessage("");

    // Cheap client-side shape check; the server is the source of truth.
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newEmail)) {
      setEmailError(t.settings.account.invalidEmail);
      return;
    }

    setEmailLoading(true);
    try {
      const res = await fetch("/api/v1/auth/update-email", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        body: JSON.stringify({
          current_password: emailPassword,
          new_email: newEmail,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        setEmailError(parseAuthError(data).message);
        return;
      }

      await refreshUser();
      setEmailMessage(t.settings.account.emailChangedSuccess);
      setNewEmail("");
      setEmailPassword("");
    } catch {
      setEmailError(t.settings.account.networkError);
    } finally {
      setEmailLoading(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setMessage("");

    if (newPassword !== confirmPassword) {
      setError(t.settings.account.passwordMismatch);
      return;
    }
    if (newPassword.length < 8) {
      setError(t.settings.account.passwordTooShort);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      setMessage(t.settings.account.passwordChangedSuccess);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch {
      setError(t.settings.account.networkError);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      <SettingsSection title={t.settings.account.profileTitle}>
        <div className="space-y-2">
          {/* The value column must be able to shrink — a `max-content` column
              lets a long email push the dialog into horizontal overflow. */}
          <div className="grid grid-cols-[max-content_minmax(0,1fr)] items-center gap-4">
            <span className="text-muted-foreground text-sm">
              {t.settings.account.email}
            </span>
            <span className="text-sm font-medium break-all">
              {user?.email ?? "—"}
            </span>
            <span className="text-muted-foreground text-sm">
              {t.settings.account.role}
            </span>
            <span className="text-sm font-medium capitalize">
              {user?.system_role ?? "—"}
            </span>
          </div>
        </div>
      </SettingsSection>

      <CreditsMeter />

      <BillingSettings />

      <ByokSettings />

      <ReferralCard />

      <SettingsSection
        title={t.settings.account.changeEmailTitle}
        description={t.settings.account.changeEmailDescription}
      >
        <form onSubmit={handleChangeEmail} className="max-w-sm space-y-3">
          <Input
            type="email"
            placeholder={t.settings.account.newEmail}
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder={t.settings.account.currentPassword}
            value={emailPassword}
            onChange={(e) => setEmailPassword(e.target.value)}
            required
          />
          {emailError && (
            <p className="text-destructive text-sm">{emailError}</p>
          )}
          {emailMessage && (
            <p className="text-success text-sm">{emailMessage}</p>
          )}
          <Button
            type="submit"
            variant="outline"
            size="sm"
            disabled={emailLoading}
          >
            {emailLoading
              ? t.settings.account.updating
              : t.settings.account.updateEmail}
          </Button>
        </form>
      </SettingsSection>

      <SettingsSection
        title={t.settings.account.changePasswordTitle}
        description={t.settings.account.changePasswordDescription}
      >
        <form onSubmit={handleChangePassword} className="max-w-sm space-y-3">
          <Input
            type="password"
            placeholder={t.settings.account.currentPassword}
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder={t.settings.account.newPassword}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={8}
          />
          <Input
            type="password"
            placeholder={t.settings.account.confirmNewPassword}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
          />
          {error && <p className="text-destructive text-sm">{error}</p>}
          {message && <p className="text-success text-sm">{message}</p>}
          <Button type="submit" variant="outline" size="sm" disabled={loading}>
            {loading
              ? t.settings.account.updating
              : t.settings.account.updatePassword}
          </Button>
        </form>
      </SettingsSection>

      <SettingsSection title="" description="">
        <Button
          variant="destructive"
          size="sm"
          onClick={logout}
          className="gap-2"
        >
          <LogOutIcon className="size-4" />
          {t.settings.account.signOut}
        </Button>
      </SettingsSection>
    </div>
  );
}
