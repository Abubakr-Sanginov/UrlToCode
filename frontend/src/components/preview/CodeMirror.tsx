import { useRef, useEffect, useMemo } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, ViewUpdate, highlightActiveLine, highlightActiveLineGutter } from "@codemirror/view";
import { espresso, cobalt } from "thememirror";
import {
  defaultKeymap,
  history,
  indentWithTab,
  redo,
  undo,
  historyKeymap,
} from "@codemirror/commands";
import { bracketMatching, indentOnInput, foldGutter, foldKeymap } from "@codemirror/language";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { javascript } from "@codemirror/lang-javascript";
import { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { EditorTheme } from "@/types";

interface Props {
  code: string;
  editorTheme: EditorTheme;
  onCodeChange: (code: string) => void;
  readOnly?: boolean;
}

function CodeMirror({ code, editorTheme, onCodeChange, readOnly = false }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);

  const theme = editorTheme === EditorTheme.ESPRESSO ? espresso : cobalt;

   
  const editorState = useMemo(
    () =>
      EditorState.create({
        extensions: [
          history(),
          keymap.of([
            ...defaultKeymap,
            ...historyKeymap,
            ...foldKeymap,
            ...searchKeymap,
            ...completionKeymap,
            ...closeBracketsKeymap,
            indentWithTab,
            { key: "Mod-z", run: undo, preventDefault: true },
            { key: "Mod-Shift-z", run: redo, preventDefault: true },
          ]),
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightActiveLine(),
          highlightSelectionMatches(),
          bracketMatching(),
          closeBrackets(),
          indentOnInput(),
          foldGutter({
            openText: "\u25BE",
            closedText: "\u25B8",
          }),
          autocompletion({
            override: [],
          }),
          html(),
          css(),
          javascript(),
          theme,
          EditorView.lineWrapping,
          EditorView.editable.of(!readOnly),
          EditorState.readOnly.of(readOnly),
          EditorView.updateListener.of((update: ViewUpdate) => {
            if (update.docChanged) {
              const updatedCode = update.state.doc.toString();
              onCodeChange(updatedCode);
            }
          }),
        ],
      }),
    // Rebuilding the state on every onCodeChange/theme identity change would
    // throw away the editor (and the cursor) on each keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [editorTheme, readOnly]
  );

   
  useEffect(() => {
    if (!ref.current) return;

    view.current = new EditorView({
      state: editorState,
      parent: ref.current,
    });

    return () => {
      if (view.current) {
        view.current.destroy();
        view.current = null;
      }
    };
    // Mount-only: the view is created once and later updated in place, so
    // depending on editorState here would destroy and rebuild it constantly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (view.current && view.current.state.doc.toString() !== code) {
      view.current.dispatch({
        changes: { from: 0, to: view.current.state.doc.length, insert: code },
      });
    }
  }, [code]);

  return (
    <div
      className="code-editor-container overflow-hidden h-full"
      ref={ref}
    />
  );
}

export default CodeMirror;
