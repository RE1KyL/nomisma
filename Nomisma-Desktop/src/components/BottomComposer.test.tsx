import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { Children, isValidElement } from "react";
import { BottomComposer } from "./BottomComposer";

vi.mock("./InputBar", () => ({
  InputBar: () => <div data-testid="input-bar">input-bar</div>,
}));

vi.mock("./PermissionPrompt", () => ({
  PermissionPrompt: ({ request }: { request: { requestId: string } }) => (
    <div data-testid="permission-prompt">{request.requestId}</div>
  ),
}));

type BottomComposerProps = ComponentProps<typeof BottomComposer>;

function createProps(
  overrides: Partial<BottomComposerProps> = {},
): BottomComposerProps {
  return {
    pendingPermission: null,
    onRespondPermission: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    isProcessing: false,
    model: "claude-sonnet-4",
    claudeEffort: "high",
    planMode: false,
    permissionMode: "default",
    onModelChange: vi.fn(),
    onClaudeModelEffortChange: vi.fn(),
    onPlanModeChange: vi.fn(),
    onPermissionModeChange: vi.fn(),
    queuedCount: 0,
    ...overrides,
  };
}

describe("BottomComposer", () => {
  it("exposes Stop during approval and routes it only to the existing onStop", () => {
    const props = createProps({ isProcessing: true, pendingPermission: {
      requestId: "req-stop", toolName: "nomisma_health_check", toolInput: {}, toolUseId: "tool-stop",
    }});
    const html = renderToStaticMarkup(<BottomComposer {...props} />);
    expect(html).toContain('aria-label="Stop current task"');
    expect(html).toContain('data-testid="permission-prompt"');
    const element = BottomComposer(props);
    for (const child of Children.toArray(element.props.children)) {
      if (!isValidElement<{ children?: React.ReactNode }>(child)) continue;
      for (const button of Children.toArray(child.props.children)) {
        if (isValidElement<{ "aria-label"?: string; onClick: () => void }>(button) && button.props["aria-label"] === "Stop current task") {
          button.props.onClick();
        }
      }
    }
    expect(props.onStop).toHaveBeenCalledTimes(1);
    expect(props.onRespondPermission).not.toHaveBeenCalled();
  });

  it("keeps ordinary running Stop in the existing input bar without a duplicate", () => {
    const html = renderToStaticMarkup(<BottomComposer {...createProps({ isProcessing: true })} />);
    expect(html).toContain('aria-hidden="false"');
    expect(html).not.toContain('aria-label="Stop current task"');
  });

  it("does not expose approval Stop after the task has settled", () => {
    const html = renderToStaticMarkup(<BottomComposer {...createProps({ pendingPermission: {
      requestId: "stale", toolName: "health", toolInput: {}, toolUseId: "tool-stale",
    } })} />);
    expect(html).not.toContain('aria-label="Stop current task"');
  });
  it("keeps the input bar mounted when there is no pending permission", () => {
    const html = renderToStaticMarkup(<BottomComposer {...createProps()} />);

    expect(html).toContain("aria-hidden=\"false\"");
    expect(html).toContain("data-testid=\"input-bar\"");
    expect(html).not.toContain("data-testid=\"permission-prompt\"");
  });

  it("keeps the input bar mounted while also rendering the permission prompt", () => {
    const html = renderToStaticMarkup(
      <BottomComposer
        {...createProps({
          pendingPermission: {
            requestId: "req-1",
            toolName: "bash",
            toolInput: {},
            toolUseId: "tool-1",
          },
        })}
      />,
    );

    expect(html).toContain("data-testid=\"permission-prompt\"");
    expect(html).toContain("req-1");
    expect(html).toContain("aria-hidden=\"true\"");
    expect(html).toContain("inert=\"\"");
    expect(html).toContain("data-testid=\"input-bar\"");
  });
});
