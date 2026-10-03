import { useCallback, useEffect, useRef, useState } from "react";
import { generateCode } from "./generateCode";
import { AppState, AppTheme, EditorTheme, Settings } from "./types";
import type { ToolPayload } from "./components/commits/types";
import { NEW_DESIGN_SYSTEM_CONTENT } from "./lib/design-systems";
import { IS_RUNNING_ON_CLOUD } from "./config";
import { OnboardingNote } from "./components/messages/OnboardingNote";
import { usePersistedState } from "./hooks/usePersistedState";
import TermsOfServiceDialog from "./components/TermsOfServiceDialog";
import { USER_CLOSE_WEB_SOCKET_CODE } from "./constants";
import toast from "react-hot-toast";
import { nanoid } from "nanoid";
import { Stack } from "./lib/stacks";
import { CodeGenerationModel } from "./lib/models";
import useBrowserTabIndicator from "./hooks/useBrowserTabIndicator";
import { LuChevronLeft } from "react-icons/lu";
import {
  buildAssistantHistoryMessage,
  buildUpdateGenerationRequest,
  buildUserHistoryMessage,
  cloneVariantHistory,
  GenerationRequest,
  registerAssetIds,
} from "./lib/prompt-history";
// import TipLink from "./components/messages/TipLink";
import { useAppStore } from "./store/app-store";
import { useProjectStore } from "./store/project-store";
import { openProject } from "./lib/accounts";
import { GENERATED_FILE_PREFIX } from "./lib/projectFiles";
import { startProject } from "./lib/payments";
import { useDesignSystems } from "./hooks/useDesignSystems";
import {
  buildSelectedElementInstruction,
  describeElementContext,
} from "./components/select-and-edit/utils";
import { useEscapeToExitSelectMode } from "./components/select-and-edit/useEscapeToExitSelectMode";
import Sidebar from "./components/sidebar/Sidebar";
import IconStrip from "./components/sidebar/IconStrip";
import { SignInDialog } from "./components/SignInDialog";
import { AdminDialog } from "./components/sidebar/AdminDialog";
import { ProjectsPage } from "./components/sidebar/ProjectsPage";
import { useAccountUi } from "./store/account-ui-store";
import HistoryDisplay from "./components/history/HistoryDisplay";
import PreviewPane from "./components/preview/PreviewPane";
import StartPane from "./components/start-pane/StartPane";
import SettingsTab from "./components/settings/SettingsTab";
import DesignSystemsModal from "./components/settings/DesignSystemsModal";
import { AiEditCommit, Commit } from "./components/commits/types";
import { createCommit } from "./components/commits/utils";
import { useUrlToCode } from "./hooks/useUrlToCode";
import { pagePathForLabel, useCloneStore } from "./store/clone-store";
import { regeneratePage } from "./lib/cloneRuns";
import { buildCodeMapFromCommits, projectFileForPage } from "./lib/projectFiles";

/** Toast id of the per-page regeneration, so a later toast replaces it. */
const REGENERATE_TOAST_ID = "clone-page-regenerate";

function App() {
  const {
    // Inputs
    inputMode,
    setInputMode,
    referenceImages,
    setReferenceImages,
    initialPrompt,
    setInitialPrompt,
    upsertPromptAssets,
    resetPromptAssets,

    head,
    commits,
    addCommit,
    removeCommit,
    setHead,
    appendCommitCode,
    setCommitCode,
    resetCommits,
    resetHead,
    updateVariantStatus,
    resizeVariants,
    setVariantModels,
    appendVariantHistoryMessage,
    startAgentEvent,
    appendAgentEventContent,
    finishAgentEvent,

    // Project files edited by hand or rewritten by a regeneration
    setFileOverride,

    // Outputs
    appendExecutionConsole,
    resetExecutionConsoles,
  } = useProjectStore();

  const {
    disableInSelectAndEditMode,
    setUpdateInstruction,
    updateImages,
    setUpdateImages,
    appState,
    setAppState,
    selectedElement,
    setSelectedElement,
  } = useAppStore();

  // Settings
  const [settings, setSettings] = usePersistedState<Settings>(
    {
      openAiApiKey: null,
      openAiBaseURL: null,
      replicateApiKey: null,
      anthropicApiKey: null,
      geminiApiKey: null,
      screenshotOneApiKey: null,
      isImageGenerationEnabled: true,
      editorTheme: EditorTheme.COBALT,
      generatedCodeConfig: Stack.HTML_TAILWIND,
      codeGenerationModel: CodeGenerationModel.GEMINI_3_FLASH_PREVIEW_MINIMAL,
      selectedDesignSystemId: null,
      // Only relevant for hosted version
      isTermOfServiceAccepted: false,
      openRouterApiKey: null,
      openRouterModel: null,
      reasoningEffort: null,
      // Custom OpenAI-compatible provider
      customProviderBaseUrl: null,
      customProviderApiKey: null,
      customProviderModel: null,
    },
    "setting"
  );
  const [appTheme, setAppTheme] = usePersistedState<AppTheme>(
    // The product is dark-first; SYSTEM/LIGHT remain available in Settings.
    AppTheme.DARK,
    "app-theme"
  );

  const wsRef = useRef<WebSocket>(null);
  // Where the sign-in dialog is opened from, and closed after a sign-in
  // succeeds.
  const isSignInOpen = useAccountUi((state) => state.isSignInOpen);
  const closeSignIn = useAccountUi((state) => state.closeSignIn);
  const isAdminOpen = useAccountUi((state) => state.isAdminOpen);
  const closeAdmin = useAccountUi((state) => state.closeAdmin);
  const openAdmin = useAccountUi((state) => state.openAdmin);

  const openSignIn = useAccountUi((state) => state.openSignIn);

  // A failed sign-in at GitHub or Google comes back to the address with a
  // message on it rather than through any in-memory channel, because the
  // browser left for the provider and everything this tab was holding is
  // gone. Reopening the dialog is what puts that message where it can be
  // read; without it the person is dropped on the start screen with no idea
  // that anything went wrong.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("signInError")) {
      openSignIn();
    }
  }, [openSignIn]);

  // First visit to the admin screen, with no link to follow: open it when
  // the address carries the marker. Once the operator has typed their
  // token in, the panel links to it from then on, and everyone else
  // never sees a mention of it.
  useEffect(() => {
    const openFromHash = () => {
      if (window.location.hash === "#admin") openAdmin();
    };
    openFromHash();
    // Typing the address is a same-document move: the page is not
    // reloaded, so the check above has already run by the time the hash
    // appears.
    window.addEventListener("hashchange", openFromHash);
    return () => window.removeEventListener("hashchange", openFromHash);
  }, [openAdmin]);
  const [isProjectsPageOpen, setIsProjectsPageOpen] = useState(false);
  const lastThinkingEventIdRef = useRef<Record<number, string>>({});
  const lastAssistantEventIdRef = useRef<Record<number, string>>({});
  const lastToolEventIdRef = useRef<Record<number, string>>({});

  // The project is restored from IndexedDB, which is asynchronous. Rendering
  // before it lands would flash the start screen over a project the user
  // already has.
  const [isProjectRestored, setIsProjectRestored] = useState(
    useProjectStore.persist.hasHydrated()
  );

  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<"preview" | "chat">("preview");
  const [isDesignSystemsModalOpen, setIsDesignSystemsModalOpen] =
    useState(false);
  const [designSystemsModalInitialId, setDesignSystemsModalInitialId] =
    useState<string | null>(null);
  const {
    designSystems,
    isLoading: areDesignSystemsLoading,
    createDesignSystem,
    updateDesignSystem,
    deleteDesignSystem,
  } = useDesignSystems();

  const setSelectedDesignSystemId = useCallback(
    (id: string | null) => {
      setSettings((prev) => ({ ...prev, selectedDesignSystemId: id }));
    },
    [setSettings]
  );

  const openDesignSystemsManager = useCallback((focusedId?: string | null) => {
    setDesignSystemsModalInitialId(focusedId ?? null);
    setIsDesignSystemsModalOpen(true);
  }, []);

  const handleAddNewDesignSystem = useCallback(async () => {
    try {
      const isFirst = designSystems.length === 0;
      const created = await createDesignSystem({
        name: `Design system ${designSystems.length + 1}`,
        content: NEW_DESIGN_SYSTEM_CONTENT,
      });
      if (isFirst) {
        setSelectedDesignSystemId(created.id);
      }
      openDesignSystemsManager(created.id);
    } catch (error) {
      console.error("Failed to create design system", error);
      toast.error("Could not create design system.");
    }
  }, [
    createDesignSystem,
    designSystems.length,
    openDesignSystemsManager,
    setSelectedDesignSystemId,
  ]);
  // Indicate coding state using the browser tab's favicon and title
  useBrowserTabIndicator(appState === AppState.CODING);

  useEscapeToExitSelectMode();

  // A restored project has to move the app out of INITIAL, or the start screen
  // covers it and the code looks lost.
  useEffect(() => {
    const finish = () => setIsProjectRestored(true);
    const unsubscribe = useProjectStore.persist.onFinishHydration(finish);
    // Covers the case where hydration already finished before this effect ran.
    if (useProjectStore.persist.hasHydrated()) finish();
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!isProjectRestored) return;
    if (Object.keys(useProjectStore.getState().commits).length === 0) return;
    setAppState(AppState.CODE_READY);
  }, [isProjectRestored, setAppState]);

  // When the user already has the settings in local storage, newly added keys
  // do not get added to the settings so if it's falsy, we populate it with the default
  // value
  useEffect(() => {
    if (!settings.generatedCodeConfig) {
      setSettings((prev) => ({
        ...prev,
        generatedCodeConfig: Stack.HTML_TAILWIND,
      }));
    }
  }, [settings.generatedCodeConfig, setSettings]);

  useEffect(() => {
    if (!("selectedDesignSystemId" in settings)) {
      setSettings((prev) => ({
        ...prev,
        selectedDesignSystemId: null,
      }));
    }
  }, [settings, setSettings]);


  useEffect(() => {
    if (
      settings.selectedDesignSystemId &&
      !areDesignSystemsLoading &&
      !designSystems.some(
        (designSystem) => designSystem.id === settings.selectedDesignSystemId
      )
    ) {
      setSettings((prev) => ({
        ...prev,
        selectedDesignSystemId: null,
      }));
    }
  }, [
    areDesignSystemsLoading,
    designSystems,
    settings.selectedDesignSystemId,
    setSettings,
  ]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const isDark =
        appTheme === AppTheme.DARK ||
        (appTheme === AppTheme.SYSTEM && mediaQuery.matches);
      document.documentElement.classList.toggle("dark", isDark);
      document.body.classList.toggle("dark", isDark);
    };

    applyTheme();

    if (appTheme !== AppTheme.SYSTEM) {
      return;
    }

    const onChange = () => applyTheme();
    mediaQuery.addEventListener("change", onChange);

    return () => {
      mediaQuery.removeEventListener("change", onChange);
    };
  }, [appTheme]);

  const getAssetsById = useCallback(() => useProjectStore.getState().assetsById, []);

  // `reset` and the clone hook reference each other, so the cancel callback is
  // held in a ref to break the initialization cycle.
  const urlToCodeRef = useRef<(() => void) | null>(null);

  const urlToCode = useUrlToCode((code) => {
    importUrlToCode(code);
  });

  urlToCodeRef.current = urlToCode.reset;

  // A project the user picked from the list arrives here rather than being
  // loaded by the list itself: this importer lives in the app, and the
  // list is its sibling. Same door the clone's own code comes through, so
  // a project opens exactly as the run that made it did.
  const pendingProject = useAccountUi((state) => state.pendingProject);
  const takePendingProject = useAccountUi((state) => state.takePendingProject);
  const setPendingProject = useAccountUi((state) => state.setPendingProject);
  const lastOpenedProject = useRef<Record<string, string> | null>(null);
  // Which project a `/start` deep link has already opened. Telegram leaves
  // start_param in place for the life of the window, so without this every
  // reload pulls the person back out of what they are doing.
  const deepLinkedTo = useRef<string | null>(null);
  // The importer is rebuilt on every render, so it is reached through a
  // ref: naming it as a dependency would re-load the project each time
  // anything else changed.
  const importProjectRef = useRef<((code: Record<string, string>) => void) | null>(
    null
  );
  importProjectRef.current = importUrlToCode;

  useEffect(() => {
    if (pendingProject !== lastOpenedProject.current) {
      lastOpenedProject.current = pendingProject;
      if (pendingProject) {
        takePendingProject();
        importProjectRef.current?.(pendingProject);
      }
    }
  }, [pendingProject, takePendingProject]);

  // `/start project_<id>` from the bot opens that project rather than the
  // front door. Once only: Telegram keeps start_param in place for as long
  // as the Mini App is open, and a reload should not yank somebody back out
  // of whatever they are working on.
  useEffect(() => {
    const runId = startProject();
    if (!runId || deepLinkedTo.current === runId) return;
    deepLinkedTo.current = runId;
    // The account has to exist first. Inside Telegram that means the
    // Mini App sign-in, and asking for a project before it has landed would
    // be asking with somebody else's cookie.
    const ready = window.setTimeout(() => {
      void openProject(runId).then(
        (project) => {
          const generatedFiles: Record<string, string> = {};
          const code: Record<string, string> = {};
          for (const [key, content] of Object.entries(project.code)) {
            if (key.startsWith(GENERATED_FILE_PREFIX)) {
              generatedFiles[key.slice(GENERATED_FILE_PREFIX.length)] = content;
            } else {
              code[key] = content;
            }
          }
          useCloneStore.getState().setCloneSource({
            baseUrl: project.baseUrl,
            runId: project.runId,
            generatedFiles,
          });
          setPendingProject(code);
        },
        (cause: unknown) => {
          // Most often: the project is not theirs. The server decides that,
          // and saying so quietly is better than opening somebody's work.
          toast.error(
            cause instanceof Error ? cause.message : "That project could not be opened."
          );
        }
      );
    }, 1200);
    return () => window.clearTimeout(ready);
  }, [setPendingProject]);

  // Functions
  const reset = () => {
    // Stop any in-flight generation so late websocket events can't mutate
    // state after the reset (e.g. flipping the app back to CODE_READY).
    cancelCodeGeneration();
    setAppState(AppState.INITIAL);
    setUpdateInstruction("");
    setUpdateImages([]);
    disableInSelectAndEditMode();
    resetExecutionConsoles();

    resetCommits();
    resetHead();
    resetPromptAssets();

    // A project opened from the projects page is handed over rather than
    // loaded directly, because that page is a sibling of this one. Any of it
    // still waiting here belongs to the run being abandoned, and importing it
    // a moment later would put the old project straight back into the editor.
    takePendingProject();

    // Inputs
    setInputMode("image");
    setReferenceImages([]);
  };

  /**
   * "New project" from the icon strip. Unlike `reset`, this also tears down an
   * in-flight clone and clears its progress panel, so the start pane comes up
   * clean instead of showing a frozen spinner from the abandoned run.
   *
   * Leaving the projects page is part of it. The page covers the app while it
   * is open, so resetting the store behind it looks like the button did
   * nothing at all - the start pane is there, just out of sight.
   */
  const startNewProject = () => {
    setIsProjectsPageOpen(false);
    urlToCodeRef.current?.();
    reset();
  };

  const regenerate = () => {
    if (head === null) {
      toast.error(
        "No current version set. Please contact support via chat or Github."
      );
      throw new Error("Regenerate called with no head");
    }

    const currentCommit = commits[head];
    if (!currentCommit) {
      toast.error("The selected version could not be found.");
      return;
    }

    if (currentCommit.type === "ai_edit") {
      regenerateUpdate(currentCommit);
      return;
    }

    if (currentCommit.type === "code_create") {
      regenerateClonePage(currentCommit);
      return;
    }

    // Re-run the initial create request.
    if (inputMode === "image" || inputMode === "video") {
      doCreate(referenceImages, inputMode);
    } else {
      doCreateFromText(initialPrompt);
    }
  };

  // Used when the user cancels the code generation
  const cancelCodeGeneration = () => {
    wsRef.current?.close?.(USER_CLOSE_WEB_SOCKET_CODE);
  };

  // Used for user-initiated cancellation and failed edit rollbacks
  const cancelCodeGenerationAndReset = (commit: Commit) => {
    // When the current commit is the first version, reset the entire app state
    if (commit.type === "ai_create") {
      reset();
    } else {
      // Otherwise, remove current commit from commits
      removeCommit(commit.hash);

      // Revert to parent commit
      const parentCommitHash = commit.parentHash;
      if (parentCommitHash) {
        setHead(parentCommitHash);
      } else {
        throw new Error("Parent commit not found");
      }

      setAppState(AppState.CODE_READY);
    }
  };

  function doGenerateCode(
    params: GenerationRequest,
    generationParentHash: string | null = head
  ) {
    // Reset the execution console
    resetExecutionConsoles();

    // Set the app state to coding during generation
    setAppState(AppState.CODING);

    const { variantHistory, ...requestParams } = params;

    const selectedDesignSystem = designSystems.find(
      (designSystem) => designSystem.id === settings.selectedDesignSystemId
    );

    // Merge settings with params
    const updatedParams = {
      ...settings,
      ...requestParams,
      designSystem: selectedDesignSystem?.content ?? null,
    };

    // Use 4 variants for create, 2 for edits to match backend counts
    // and avoid a flash when the backend sends the actual variant count
    const initialVariantCount =
      requestParams.generationType === "create" ? 4 : 2;
    const baseCommitObject = {
      variants: Array(initialVariantCount)
        .fill(null)
        .map(() => ({
          code: "",
          history: cloneVariantHistory(variantHistory),
        })),
    };

    const commitInputObject =
      requestParams.generationType === "create"
        ? {
            ...baseCommitObject,
            type: "ai_create" as const,
            parentHash: null,
            inputs: requestParams.prompt,
          }
        : {
            ...baseCommitObject,
            type: "ai_edit" as const,
            parentHash: generationParentHash,
            inputs: requestParams.prompt,
          };

    // Create a new commit and set it as the head
    const commit = createCommit(commitInputObject);
    addCommit(commit);
    setHead(commit.hash);

    lastThinkingEventIdRef.current = {};
    lastAssistantEventIdRef.current = {};
    lastToolEventIdRef.current = {};

    const finishThinkingEvent = (variantIndex: number, status: "complete" | "error") => {
      const eventId = lastThinkingEventIdRef.current[variantIndex];
      if (!eventId) return;
      finishAgentEvent(commit.hash, variantIndex, eventId, {
        status,
        endedAt: Date.now(),
      });
      delete lastThinkingEventIdRef.current[variantIndex];
    };

    const finishAssistantEvent = (variantIndex: number, status: "complete" | "error") => {
      const eventId = lastAssistantEventIdRef.current[variantIndex];
      if (!eventId) return;
      finishAgentEvent(commit.hash, variantIndex, eventId, {
        status,
        endedAt: Date.now(),
      });
      delete lastAssistantEventIdRef.current[variantIndex];
    };

    const finishToolEvent = (variantIndex: number, status: "complete" | "error") => {
      const eventId = lastToolEventIdRef.current[variantIndex];
      if (!eventId) return;
      finishAgentEvent(commit.hash, variantIndex, eventId, {
        status,
        endedAt: Date.now(),
      });
      delete lastToolEventIdRef.current[variantIndex];
    };

    const finishInFlightEvents = (status: "complete" | "error") => {
      Object.keys(lastThinkingEventIdRef.current).forEach((key) => {
        finishThinkingEvent(Number(key), status);
      });
      Object.keys(lastAssistantEventIdRef.current).forEach((key) => {
        finishAssistantEvent(Number(key), status);
      });
      Object.keys(lastToolEventIdRef.current).forEach((key) => {
        finishToolEvent(Number(key), status);
      });
    };

    generateCode(wsRef, updatedParams, {
      onChange: (token, variantIndex) => {
        appendCommitCode(commit.hash, variantIndex, token);
      },
      onSetCode: (code, variantIndex) => {
        setCommitCode(commit.hash, variantIndex, code);
      },
      onStatusUpdate: (line, variantIndex) =>
        appendExecutionConsole(variantIndex, line),
      onVariantComplete: (variantIndex) => {
        console.log(`Variant ${variantIndex} complete event received`);
        updateVariantStatus(commit.hash, variantIndex, "complete");
        const currentCode =
          useProjectStore.getState().commits[commit.hash]?.variants[variantIndex]
            ?.code || "";
        if (currentCode.trim().length > 0) {
          appendVariantHistoryMessage(
            commit.hash,
            variantIndex,
            buildAssistantHistoryMessage(currentCode)
          );
        }
        finishThinkingEvent(variantIndex, "complete");
        finishAssistantEvent(variantIndex, "complete");
        finishToolEvent(variantIndex, "complete");
        if (commit.type === "ai_edit") {
          const {
            updateInstruction: currentInstruction,
            updateImages: currentImages,
          } = useAppStore.getState();
          const instructionUnchanged =
            currentInstruction === commit.inputs.text;
          const imagesUnchanged =
            currentImages.length === commit.inputs.images.length &&
            currentImages.every(
              (image, index) => image === commit.inputs.images[index]
            );

          // This conditional clear handles three UX scenarios:
          // 1) All variants fail: no completion event, so keep prompt/images for retry.
          // 2) A variant completes and user has typed/changed images: do not clear.
          // 3) A variant completes and user has not changed draft: clear for next edit.
          if (instructionUnchanged && imagesUnchanged) {
            setUpdateInstruction("");
            setUpdateImages([]);
          }
        }
      },
      onVariantError: (variantIndex, error) => {
        console.error(`Error in variant ${variantIndex}:`, error);
        updateVariantStatus(commit.hash, variantIndex, "error", error);
        finishThinkingEvent(variantIndex, "error");
        finishAssistantEvent(variantIndex, "error");
        finishToolEvent(variantIndex, "error");
      },
      onVariantCount: (count) => {
        console.log(`Backend is using ${count} variants`);
        resizeVariants(commit.hash, count);
      },
      onVariantModels: (models) => {
        setVariantModels(commit.hash, models);
      },
      onThinking: (content, variantIndex, eventId) => {
        if (!eventId) return;
        lastThinkingEventIdRef.current[variantIndex] = eventId;
        startAgentEvent(commit.hash, variantIndex, {
          id: eventId,
          type: "thinking",
          status: "running",
          startedAt: Date.now(),
        });
        appendAgentEventContent(commit.hash, variantIndex, eventId, content);
      },
      onAssistant: (content, variantIndex, eventId) => {
        if (!eventId) return;
        lastAssistantEventIdRef.current[variantIndex] = eventId;
        startAgentEvent(commit.hash, variantIndex, {
          id: eventId,
          type: "assistant",
          status: "running",
          startedAt: Date.now(),
        });
        appendAgentEventContent(commit.hash, variantIndex, eventId, content);
      },
      onToolStart: (data, variantIndex, eventId) => {
        if (!eventId) return;
        const lastThinking = lastThinkingEventIdRef.current[variantIndex];
        if (lastThinking && lastThinking !== eventId) {
          finishThinkingEvent(variantIndex, "complete");
        }
        const lastAssistant = lastAssistantEventIdRef.current[variantIndex];
        if (lastAssistant && lastAssistant !== eventId) {
          finishAssistantEvent(variantIndex, "complete");
        }
        startAgentEvent(commit.hash, variantIndex, {
          id: eventId,
          type: "tool",
          status: "running",
          toolName: typeof data?.name === "string" ? data.name : undefined,
          input: (data?.input as ToolPayload | undefined) ?? undefined,
          startedAt: Date.now(),
        });
        lastToolEventIdRef.current[variantIndex] = eventId;
      },
      onToolResult: (data, variantIndex, eventId) => {
        if (!eventId) return;
        finishAgentEvent(commit.hash, variantIndex, eventId, {
          status: data?.ok === false ? "error" : "complete",
          output: (data?.output as ToolPayload | undefined) ?? undefined,
          endedAt: Date.now(),
        });
        if (lastToolEventIdRef.current[variantIndex] === eventId) {
          delete lastToolEventIdRef.current[variantIndex];
        }
      },
      onCancel: (reason, errorMessage) => {
        // The project may have been reset while this generation was still in
        // flight — a stale cancellation must not mutate app state.
        if (!useProjectStore.getState().commits[commit.hash]) return;

        // Close any running agent events when the socket ends without per-event
        // terminal messages, otherwise they remain stuck in "running" state.
        finishInFlightEvents(reason === "request_failed" ? "error" : "complete");

        if (reason === "request_failed" && commit.type === "ai_create") {
          const latestCreateCommit = useProjectStore.getState().commits[commit.hash];
          latestCreateCommit?.variants.forEach((variant, variantIndex) => {
            if (variant.status === "generating") {
              updateVariantStatus(
                commit.hash,
                variantIndex,
                "error",
                errorMessage || "Generation failed. Please retry."
              );
            }
          });
          setAppState(AppState.CODE_READY);
          return;
        }

        cancelCodeGenerationAndReset(commit);
      },
      onComplete: () => {
        // Same guard as onCancel: a generation finishing after its project
        // was reset must not pull the app back into the editor.
        if (!useProjectStore.getState().commits[commit.hash]) return;
        finishInFlightEvents("complete");
        setAppState(AppState.CODE_READY);
      },
    });
  }

  // Initial version creation
  function doCreate(
    referenceImages: string[],
    inputMode: "image" | "video",
    textPrompt: string = "",
    isAssetExtractionEnabled = true
  ) {
    // Reset any existing state
    reset();

    // Set the input states
    setReferenceImages(referenceImages);
    setInputMode(inputMode);

    // Kick off the code generation
    if (referenceImages.length > 0) {
      const media =
        inputMode === "video" ? [referenceImages[0]] : referenceImages;
      const imageAssetIds =
        inputMode === "image"
          ? registerAssetIds(
              "image",
              media,
              getAssetsById,
              upsertPromptAssets,
              nanoid
            )
          : [];
      const videoAssetIds =
        inputMode === "video"
          ? registerAssetIds(
              "video",
              media,
              getAssetsById,
              upsertPromptAssets,
              nanoid
            )
          : [];
      const variantHistory = [
        buildUserHistoryMessage(textPrompt, imageAssetIds, videoAssetIds),
      ];
      doGenerateCode({
        generationType: "create",
        inputMode,
        prompt: {
          text: textPrompt,
          images: inputMode === "image" ? media : [],
          videos: inputMode === "video" ? media : [],
        },
        // Asset extraction operates on still screenshots. Video data uses the
        // same transport shape for Gemini, so explicitly disable extraction
        // instead of letting the agent try to crop a video payload.
        isAssetExtractionEnabled:
          inputMode === "image" && isAssetExtractionEnabled,
        variantHistory,
      });
    }
  }

  function doCreateFromText(text: string) {
    // Reset any existing state
    reset();

    setInputMode("text");
    setInitialPrompt(text);
    doGenerateCode({
      generationType: "create",
      inputMode: "text",
      prompt: { text, images: [], videos: [] },
      variantHistory: [buildUserHistoryMessage(text)],
    });
  }

  function regenerateUpdate(commit: AiEditCommit) {
    const parentHash = commit.parentHash;
    const parentCommit = parentHash ? commits[parentHash] : null;
    if (!parentHash || !parentCommit) {
      toast.error("The previous version needed to retry this edit was not found.");
      return;
    }

    const parentVariant =
      parentCommit.variants[parentCommit.selectedVariantIndex];
    if (!parentVariant) {
      toast.error("The selected option from the previous version was not found.");
      return;
    }

    const imageAssetIds = registerAssetIds(
      "image",
      commit.inputs.images,
      getAssetsById,
      upsertPromptAssets,
      nanoid
    );

    doGenerateCode(
      buildUpdateGenerationRequest({
        inputMode,
        prompt: commit.inputs,
        parentCommit,
        imageAssetIds,
        getAssetsById,
      }),
      parentHash
    );
  }

  // Subsequent updates
  async function doUpdate(updateInstruction: string) {
    if (updateInstruction.trim() === "") {
      toast.error("Please include some instructions for AI on what to update.");
      return;
    }

    if (head === null) {
      toast.error(
        "No current version set. Contact support or open a Github issue."
      );
      throw new Error("Update called with no head");
    }

    const currentCommit = commits[head];
    if (!currentCommit) {
      toast.error("The selected version could not be found.");
      return;
    }

    let modifiedUpdateInstruction = updateInstruction;
    let selectedElementHtml: string | undefined;

    // Send in a reference to the selected element if it exists. Selection
    // visuals are overlays, so the element's outerHTML is already clean.
    if (selectedElement) {
      const elementHtml = selectedElement.outerHTML;
      selectedElementHtml = elementHtml;
      modifiedUpdateInstruction = buildSelectedElementInstruction(
        updateInstruction,
        elementHtml,
        selectedElement.isConnected
          ? describeElementContext(selectedElement)
          : undefined
      );
      setSelectedElement(null);
    }

    const updateImageAssetIds = registerAssetIds(
      "image",
      updateImages,
      getAssetsById,
      upsertPromptAssets,
      nanoid
    );

    doGenerateCode(
      buildUpdateGenerationRequest({
        inputMode,
        prompt: {
          text: updateInstruction,
          fullText: modifiedUpdateInstruction,
          images: updateImages,
          videos: [],
          selectedElementHtml,
        },
        parentCommit: currentCommit,
        imageAssetIds: updateImageAssetIds,
        getAssetsById,
      })
    );
  }

  const handleTermDialogOpenChange = (open: boolean) => {
    setSettings((s) => ({
      ...s,
      isTermOfServiceAccepted: !open,
    }));
  };

  /**
   * Generate one page of a finished clone again.
   *
   * A clone's pages arrive as `code_create` commits and used to be a dead end
   * for regeneration: the run that produced them lived only inside the
   * websocket that had already closed. The backend keeps the run - the crawl
   * and every page it generated - so one page can be rebuilt from the original
   * markup without crawling the site or paying for the other pages again.
   */
  async function regenerateClonePage(commit: Commit) {
    const runId = useCloneStore.getState().runId;
    if (!runId) {
      toast.error(
        "This clone's run is no longer on the server, so it cannot be regenerated. Clone the site again.",
        { duration: 6000 }
      );
      return;
    }

    const path = pagePathForLabel(commit.label);
    if (!path) {
      toast.error(
        "This page is not a crawled page of the clone, so it cannot be regenerated."
      );
      return;
    }

    // An edit typed into the prompt box turns a regeneration into a repair:
    // the model is shown the page as it stands and asked for that change
    // specifically, instead of a fresh page that discards the user's work.
    const instruction = useAppStore.getState().updateInstruction.trim();
    toast.loading(
      instruction ? `Fixing ${path}...` : `Regenerating ${path}...`,
      { id: REGENERATE_TOAST_ID }
    );

    try {
      const code = await regeneratePage({
        runId,
        path,
        settings,
        instruction: instruction || undefined,
        stack: useCloneStore.getState().cloneStack,
      });
      // The page is one file of the project, so its new code lands as an
      // override on top of the commit: the rest of the clone is untouched and
      // the page switcher keeps its shape. The key is the project file path,
      // not the route, which is what the editor and the ZIP both use.
      const codeMap = buildCodeMapFromCommits(useProjectStore.getState().commits);
      const filePath = projectFileForPage(codeMap, path);
      if (!filePath) {
        toast.error(`Could not work out which file holds ${path}.`, {
          id: REGENERATE_TOAST_ID,
        });
        return;
      }
      setFileOverride(filePath, code);
      setUpdateInstruction("");
      toast.success(`Regenerated ${path}.`, { id: REGENERATE_TOAST_ID });
    } catch (error) {
      // The server says what the provider objected to (a bad key, a retired
      // model, a rate limit); repeating that beats a generic failure.
      const message =
        error instanceof Error ? error.message : "The page could not be regenerated.";
      toast.error(message, { id: REGENERATE_TOAST_ID, duration: 6000 });
    }
  }

  /**
   * Turns a completed clone into one version per generated page.
   *
   * The portal/landing page comes first and becomes the visible head; every
   * crawled page follows in crawl order so the version switcher doubles as a
   * page switcher. Previously only a single entry was kept and the rest of the
   * run's output was discarded.
   */
  function importUrlToCode(code: Record<string, string>) {
    reset();

    const portalKey = "project-structure";
    const pageKeys = Object.keys(code).filter(
      (key) => key !== portalKey && key !== "database-schema" && code[key]?.trim()
    );

    const ordered: { label: string; code: string }[] = [];
    if (code[portalKey]?.trim()) {
      ordered.push({ label: "Portal", code: code[portalKey] });
    }
    for (const key of pageKeys) {
      ordered.push({ label: key, code: code[key] });
    }

    if (ordered.length === 0) {
      toast.error("The run produced no usable code.");
      return;
    }

    // Newest commit wins the "latest" slot, so append in reverse and point the
    // head at the portal to leave the user on the entry page.
    let firstHash: string | null = null;
    for (let i = ordered.length - 1; i >= 0; i--) {
      const entry = ordered[i];
      const commit = createCommit({
        type: "code_create",
        parentHash: null,
        label: entry.label,
        variants: [
          {
            code: entry.code,
            history: [],
            status: "complete",
            completedAt: Date.now(),
          },
        ],
        inputs: null,
      });
      addCommit(commit);
      if (i === 0) firstHash = commit.hash;
    }

    if (firstHash) setHead(firstHash);
    setAppState(AppState.CODE_READY);

    const pageCount = ordered.length;
    toast.success(
      `Imported ${pageCount} ${pageCount === 1 ? "page" : "pages"}`
    );
  }

  const showContentPanel =
    appState === AppState.CODING ||
    appState === AppState.CODE_READY ||
    isHistoryOpen;
  const isCodingOrReady =
    appState === AppState.CODING || appState === AppState.CODE_READY;
  const showMobileChatPane = showContentPanel && mobilePane === "chat";

  // Reading from IndexedDB is a round trip; show nothing rather than the start
  // screen for the few frames it takes.
  if (!isProjectRestored) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas text-foreground">
        <span className="font-mono text-xs text-muted-foreground">
          Restoring project…
        </span>
      </div>
    );
  }

  return (
    <div
      className={`bg-canvas text-foreground ${
        appState === AppState.CODING || appState === AppState.CODE_READY
          ? "flex h-dvh flex-col overflow-hidden lg:block lg:h-screen"
          : "flex min-h-screen flex-col"
      }`}
    >
      {IS_RUNNING_ON_CLOUD && (
        <TermsOfServiceDialog
          open={!settings.isTermOfServiceAccepted}
          onOpenChange={handleTermDialogOpenChange}
        />
      )}

      {/* One sign-in dialog for the whole app. The clone pane and the icon
          strip both open it, so it cannot live inside either of them. */}
      {isSignInOpen && <SignInDialog onSignedIn={closeSignIn} />}
      {isAdminOpen && <AdminDialog onClose={closeAdmin} />}      {/* Icon strip - always visible */}
      <div
        className="sticky top-0 z-50 lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:w-16 lg:flex-col"
      >
        <IconStrip
          isHistoryOpen={isHistoryOpen}
          isEditorOpen={!isHistoryOpen && !isSettingsOpen && !isProjectsPageOpen}
          isSettingsOpen={isSettingsOpen}
          showHistory={isCodingOrReady}
          showEditor={isCodingOrReady}
          isProjectsOpen={isProjectsPageOpen}
          onToggleProjects={() => {
            setIsProjectsPageOpen((prev) => !prev);
            setIsHistoryOpen(false);
            setIsSettingsOpen(false);
          }}
          onToggleHistory={() => {
            setIsHistoryOpen((prev) => !prev);
            setIsSettingsOpen(false);
            setMobilePane("chat");
          }}
          onToggleEditor={() => {
            setIsProjectsPageOpen(false);
            setIsHistoryOpen(false);
            setIsSettingsOpen(false);
            setMobilePane("preview");
          }}
          onLogoClick={() => {
            setIsProjectsPageOpen(false);
            setIsHistoryOpen(false);
            setIsSettingsOpen(false);
            setMobilePane("preview");
          }}
          onNewProject={() => {
            startNewProject();
            setIsHistoryOpen(false);
            setIsSettingsOpen(false);
            setMobilePane("preview");
          }}
          onOpenSettings={() => {
            setIsSettingsOpen(true);
            setIsHistoryOpen(false);
          }}
        />
      </div>

      {isCodingOrReady && !isSettingsOpen && (
        <div className="border-b border-border bg-card px-4 py-2 lg:hidden">
          <div
            className="grid grid-cols-2 rounded-lg border border-border bg-muted p-0.5"
            role="tablist"
            aria-label="Mobile view"
          >
            <button
              type="button"
              role="tab"
              aria-selected={mobilePane === "preview"}
              onClick={() => {
                setIsHistoryOpen(false);
                setMobilePane("preview");
              }}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                mobilePane === "preview"
                  ? "bg-card text-foreground shadow-card"
                  : "text-muted-foreground"
              }`}
            >
              Preview
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mobilePane === "chat"}
              onClick={() => setMobilePane("chat")}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                mobilePane === "chat"
                  ? "bg-card text-foreground shadow-card"
                  : "text-muted-foreground"
              }`}
            >
              Chat
            </button>
          </div>
        </div>
      )}

      {/* Content panel - shows sidebar, history, or editor */}
      {showContentPanel && !isSettingsOpen && (
        <div
          className={`border-b border-border bg-card lg:fixed lg:inset-y-0 lg:left-16 lg:z-40 lg:flex lg:w-[calc(28rem-4rem)] lg:flex-col lg:border-b-0 lg:border-r ${
            showMobileChatPane ? "block" : "hidden lg:flex"
          }`}
        >
            {isHistoryOpen ? (
              <div className="sidebar-scrollbar-stable flex-1 overflow-y-auto px-4">
                <div className="mt-3">
                  <div className="mb-3 flex items-center justify-between px-1">
                    <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Versions
                    </h2>
                    <button
                      type="button"
                      onClick={() => setIsHistoryOpen(false)}
                      className="flex items-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <LuChevronLeft className="w-3.5 h-3.5" />
                      Back to editor
                    </button>
                  </div>
                  <HistoryDisplay />
                </div>
              </div>
            ) : (
              <>
                {IS_RUNNING_ON_CLOUD && !settings.openAiApiKey && (
                  <div className="px-6 mt-4">
                    <OnboardingNote />
                  </div>
                )}

                {(appState === AppState.CODING ||
                  appState === AppState.CODE_READY) && (
                  <Sidebar
                    doUpdate={doUpdate}
                    regenerate={regenerate}
                    cancelCodeGeneration={cancelCodeGeneration}
                    designSystem={{
                      designSystems,
                      selectedDesignSystemId: settings.selectedDesignSystemId,
                      setSelectedDesignSystemId,
                      onAddNew: handleAddNewDesignSystem,
                      onManage: () => openDesignSystemsManager(),
                    }}
                    onOpenVersions={() => {
                      setIsHistoryOpen(true);
                      setMobilePane("chat");
                    }}
                  />
                )}
              </>
            )}
        </div>
      )}

      <main
        className={`${
          isSettingsOpen
            ? "flex flex-1 min-h-0 flex-col lg:h-full lg:pl-16"
            : showContentPanel
              ? "flex flex-1 min-h-0 flex-col lg:h-full lg:pl-[28rem]"
              : "lg:pl-16"
        } ${isCodingOrReady && !isSettingsOpen && mobilePane === "chat" ? "hidden lg:flex" : ""}`}
      >
        {isSettingsOpen ? (
          <SettingsTab
            settings={settings}
            setSettings={setSettings}
            appTheme={appTheme}
            setAppTheme={setAppTheme}
          />
        ) : (
          <>
            {isProjectsPageOpen && (
              <ProjectsPage
                onOpened={() => setIsProjectsPageOpen(false)}
                // The button promises to go and clone something, so it has
                // to leave this page behind - closing it on its own would
                // drop somebody back onto the project they came to leave.
                onGoToClone={() => startNewProject()}
              />
            )}

            {!isProjectsPageOpen && appState === AppState.INITIAL && (
              <StartPane
                startCrawl={urlToCode.startCrawl}
                cancelCrawl={urlToCode.cancelCrawl}
                choosePages={urlToCode.choosePages}
                state={urlToCode.state}
                settings={settings}
              />
            )}

            {!isProjectsPageOpen && isCodingOrReady && (
              <PreviewPane
                settings={settings}
                onOpenVersions={() => {
                  setIsHistoryOpen(true);
                  setMobilePane("chat");
                }}
                onRegeneratePage={() => {
                  const commit = head ? commits[head] : undefined;
                  if (commit) void regenerateClonePage(commit);
                }}
              />
            )}
          </>
        )}
      </main>

      <DesignSystemsModal
        open={isDesignSystemsModalOpen}
        onOpenChange={setIsDesignSystemsModalOpen}
        designSystems={designSystems}
        selectedDesignSystemId={settings.selectedDesignSystemId}
        setSelectedDesignSystemId={setSelectedDesignSystemId}
        initialEditingId={designSystemsModalInitialId}
        createDesignSystem={createDesignSystem}
        updateDesignSystem={updateDesignSystem}
        deleteDesignSystem={deleteDesignSystem}
      />
    </div>
  );
}

export default App;
