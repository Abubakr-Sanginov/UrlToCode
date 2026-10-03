import { useAccountUi } from "./account-ui-store";

/**
 * The handover between the project list and the editor.
 *
 * These cover the handoff itself, not the loading: the list and the app
 * are siblings, so the code is parked here and taken by whoever can
 * actually load it. A project that is picked but never handed over is
 * exactly the "I clicked Open and nothing happened" case.
 */
beforeEach(() => {
  useAccountUi.setState({
    pendingProject: null,
  });
});

it("carries the code of the project that was picked", () => {
  const code = { "project-structure": "<html></html>", "/": "<html></html>" };

  useAccountUi.getState().setPendingProject(code);

  expect(useAccountUi.getState().pendingProject).toEqual(code);
});

it("hands it over once, not every time it is read", () => {
  useAccountUi.getState().setPendingProject({ "/": "<html></html>" });

  expect(useAccountUi.getState().takePendingProject()).toEqual({
    "/": "<html></html>",
  });
  // Read again: nothing left, or the same project would reopen by itself
  // the next time anything looked here.
  expect(useAccountUi.getState().takePendingProject()).toBeNull();
});

it("leaves the account window alone when admin opens", () => {
  useAccountUi.setState({ isPanelOpen: true });

  useAccountUi.getState().openAdmin();

  expect(useAccountUi.getState().isAdminOpen).toBe(true);
  expect(useAccountUi.getState().isPanelOpen).toBe(false);
});

it("stays out of the way when admin closes", () => {
  useAccountUi.getState().openAdmin();

  useAccountUi.getState().closeAdmin();

  expect(useAccountUi.getState().isAdminOpen).toBe(false);
});