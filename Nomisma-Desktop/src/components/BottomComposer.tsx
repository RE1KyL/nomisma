import type { ComponentProps } from "react";
import { InputBar } from "./InputBar";
import { PermissionPrompt } from "./PermissionPrompt";
import { Button } from "./ui/button";
import { Square } from "lucide-react";

type InputBarProps = ComponentProps<typeof InputBar>;
type PermissionPromptProps = ComponentProps<typeof PermissionPrompt>;

interface BottomComposerProps extends InputBarProps {
  pendingPermission: PermissionPromptProps["request"] | null;
  onRespondPermission: PermissionPromptProps["onRespond"];
}

export function BottomComposer({
  pendingPermission,
  onRespondPermission,
  ...inputBarProps
}: BottomComposerProps) {
  const hasPendingPermission = !!pendingPermission;

  return (
    <>
      {pendingPermission ? (
        <PermissionPrompt
          key={pendingPermission.requestId}
          request={pendingPermission}
          onRespond={onRespondPermission}
        />
      ) : null}
      {hasPendingPermission && inputBarProps.isProcessing && (
        <div className="pointer-events-auto mx-auto flex justify-end px-4 pb-2">
          <Button size="sm" variant="outline" onClick={inputBarProps.onStop} aria-label="Stop current task">
            <Square className="h-3 w-3" /> Stop
          </Button>
        </div>
      )}
      <div
        hidden={hasPendingPermission}
        aria-hidden={hasPendingPermission}
        inert={hasPendingPermission || undefined}
      >
        <InputBar {...inputBarProps} />
      </div>
    </>
  );
}
