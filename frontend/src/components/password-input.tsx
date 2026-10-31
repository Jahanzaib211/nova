"use client";

import { EyeIcon, EyeOffIcon } from "lucide-react";
import * as React from "react";

import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * A password field with an inline show/hide toggle. Accepts every prop a
 * plain `<Input>` does except `type`, which the toggle owns; `className`
 * styles the outer group so callers can size it like any other input.
 */
function PasswordInput({
  className,
  disabled,
  ...props
}: Omit<React.ComponentProps<"input">, "type">) {
  const { t } = useI18n();
  const [visible, setVisible] = React.useState(false);

  return (
    // InputGroup's own surface is the opaque chat-box style; match the plain
    // Input instead so a password field looks like the email field above it.
    <InputGroup
      className={cn(
        "border-input dark:bg-input/30 has-[[data-slot=input-group-control]:focus-visible]:border-ring bg-transparent",
        className,
      )}
      data-disabled={disabled}
    >
      <InputGroupInput
        type={visible ? "text" : "password"}
        disabled={disabled}
        {...props}
      />
      <InputGroupAddon align="inline-end">
        <InputGroupButton
          size="icon-xs"
          // A fixed name plus aria-pressed: flipping the name as well would
          // be announced as "Hide password, pressed".
          aria-label={t.common.showPassword}
          aria-pressed={visible}
          disabled={disabled}
          onClick={() => setVisible((v) => !v)}
        >
          {visible ? <EyeOffIcon /> : <EyeIcon />}
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  );
}

export { PasswordInput };
