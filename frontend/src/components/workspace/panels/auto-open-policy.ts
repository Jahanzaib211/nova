/**
 * Decides when the Agent's Computer panel may auto-open.
 *
 * Rules (issue: "thinking side box auto expands"):
 * - The panel opens only when the agent actually uses its computer (first
 *   tool activity of a run) — never for plain thinking/chat turns.
 * - If the user closes the panel mid-run, it stays closed for that run.
 * - Each new run re-arms the auto-open exactly once.
 *
 * Pure state machine so the policy is unit-testable without React.
 */
export class AgentComputerAutoOpenPolicy {
  private autoOpened = false;
  private userClosed = false;
  private running = false;

  /** Feed every change of the thread's loading state. */
  onRunStateChange(isLoading: boolean): void {
    if (isLoading && !this.running) {
      this.autoOpened = false;
      this.userClosed = false;
    }
    this.running = isLoading;
  }

  /** Feed every change of the panel's open state. */
  onPanelOpenChange(open: boolean, prevOpen: boolean): void {
    if (prevOpen && !open && this.running) {
      this.userClosed = true;
    }
  }

  /**
   * Call on each tool-activity event. Returns true exactly once per run,
   * and never after the user closed the panel during that run.
   */
  shouldAutoOpen(): boolean {
    if (this.autoOpened || this.userClosed) {
      return false;
    }
    this.autoOpened = true;
    return true;
  }
}
