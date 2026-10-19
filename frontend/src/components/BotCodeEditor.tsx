import { useTranslation } from 'react-i18next';
import CodeMirror from '@uiw/react-codemirror';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';

interface BotCodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  height?: string;
  /** Accessible name; defaults to the bot editor's. */
  ariaLabel?: string;
}

export function BotCodeEditor({
  value,
  onChange,
  id,
  height = '256px',
  ariaLabel,
}: BotCodeEditorProps) {
  const { t } = useTranslation();
  return (
    <div className="w-full overflow-hidden rounded-md border border-input">
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={[python()]}
        theme={oneDark}
        height={height}
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: true,
        }}
        className="text-sm"
        id={id}
        aria-label={ariaLabel ?? t('settings.fanout.bot.editorAria')}
      />
    </div>
  );
}
