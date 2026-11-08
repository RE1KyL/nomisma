import { useEffect } from "react";
import {ModelProviderSetup} from "./components/ModelProviderSetup";
import { WorkspaceApp } from '@/components/workspace/WorkspaceApp';
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { syncAnalyticsSettings } from "@/lib/posthog";

export function App() {
  // Sync analytics opt-in state after mount — avoids blocking first paint with IPC calls
  useEffect(() => {
    syncAnalyticsSettings();
  }, []);
  // Guard: if the preload script failed, window.claude won't exist.
  // Throwing here lets the ErrorBoundary show a visible message instead of a blank window.
  if (!window.claude) {
    throw new Error(
      "window.claude is not available — the preload script likely failed to load. " +
      "Check the Electron console for errors.",
    );
  }
  if(!window.workspace)return <div role="alert" style={{padding:40}}>工作台接口已更新。请完全退出旧 Nomisma 窗口及启动器后重新启动。</div>;

  return (
    <TooltipProvider>
      <ModelProviderSetup><WorkspaceApp /></ModelProviderSetup>
      <Toaster
        position="top-right"
        toastOptions={{
          className: "bg-background/90 backdrop-blur-md border border-border text-foreground shadow-lg",
        }}
      />
    </TooltipProvider>
  );
}
