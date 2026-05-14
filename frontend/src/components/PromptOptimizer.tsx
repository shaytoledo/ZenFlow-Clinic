"use client";

import { useState } from "react";
import { Sparkles, Copy, Check, Loader2, History, Trash2 } from "lucide-react";
import { clsx } from "clsx";
import { api, type TargetModel, type PromptHistoryItem } from "@/lib/api";

const MODELS: { value: TargetModel; label: string; color: string }[] = [
  { value: "claude", label: "Claude", color: "bg-orange-100 text-orange-700 border-orange-200" },
  { value: "gpt-4", label: "GPT-4", color: "bg-green-100 text-green-700 border-green-200" },
  { value: "gemini", label: "Gemini", color: "bg-blue-100 text-blue-700 border-blue-200" },
];

interface PromptOptimizerProps {
  projectId: string;
  initialHistory: PromptHistoryItem[];
}

export default function PromptOptimizer({ projectId, initialHistory }: PromptOptimizerProps) {
  const [input, setInput] = useState("");
  const [model, setModel] = useState<TargetModel>("claude");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [history, setHistory] = useState<PromptHistoryItem[]>(initialHistory);
  const [showHistory, setShowHistory] = useState(false);

  async function handleOptimize(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.prompts.optimize(projectId, input.trim(), model);
      setResult(res.optimized_prompt);
      setHistory((prev) => [
        {
          id: res.history_id,
          userInput: input.trim(),
          optimizedPrompt: res.optimized_prompt,
          targetModel: model,
          createdAt: new Date().toISOString(),
        },
        ...prev,
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleCopy() {
    if (!result) return;
    await navigator.clipboard.writeText(result);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function handleDeleteHistory(id: string) {
    await api.prompts.delete(projectId, id);
    setHistory((prev) => prev.filter((h) => h.id !== id));
    if (history.find((h) => h.id === id)?.optimizedPrompt === result) setResult(null);
  }

  function loadFromHistory(item: PromptHistoryItem) {
    setInput(item.userInput);
    setResult(item.optimizedPrompt);
    setModel(item.targetModel as TargetModel);
    setShowHistory(false);
  }

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
            <Sparkles size={16} className="text-brand-500" />
            Prompt Optimizer
          </h2>
          <button
            onClick={() => setShowHistory((v) => !v)}
            className={clsx(
              "flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border transition-colors",
              showHistory
                ? "bg-brand-50 text-brand-600 border-brand-200"
                : "text-gray-500 border-gray-200 hover:bg-gray-50"
            )}
          >
            <History size={13} />
            History ({history.length})
          </button>
        </div>

        <form onSubmit={handleOptimize} className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Your simple request
            </label>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleOptimize(e);
              }}
              rows={3}
              placeholder="e.g. Write an Instagram post about acupuncture for athletes after a workout"
              className="w-full text-sm px-3 py-2.5 rounded-lg border border-gray-200 focus:border-brand-500 focus:outline-none resize-none"
              disabled={loading}
            />
            <p className="text-xs text-gray-400 mt-1">Tip: Press Ctrl+Enter to optimize</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-2">Target model</label>
            <div className="flex gap-2">
              {MODELS.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  onClick={() => setModel(m.value)}
                  className={clsx(
                    "px-3 py-1.5 rounded-lg text-xs font-medium border transition-all",
                    model === m.value ? m.color : "bg-white text-gray-500 border-gray-200 hover:bg-gray-50"
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          <button
            type="submit"
            disabled={!input.trim() || loading}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                Optimizing…
              </>
            ) : (
              <>
                <Sparkles size={16} />
                Optimize Prompt
              </>
            )}
          </button>
        </form>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {result && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
            <span className="text-sm font-semibold text-gray-700">Optimized Prompt</span>
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-brand-500 transition-colors"
            >
              {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
          <pre className="px-5 py-4 text-sm text-gray-800 whitespace-pre-wrap leading-relaxed font-sans">
            {result}
          </pre>
        </div>
      )}

      {showHistory && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-100">
            <h3 className="text-sm font-semibold text-gray-700">Prompt History</h3>
          </div>
          {history.length === 0 ? (
            <p className="px-5 py-6 text-sm text-gray-400 text-center">No history yet</p>
          ) : (
            <div className="divide-y divide-gray-100 max-h-96 overflow-y-auto">
              {history.map((item) => (
                <div
                  key={item.id}
                  className="px-5 py-3 hover:bg-gray-50 cursor-pointer group flex items-start justify-between gap-3"
                  onClick={() => loadFromHistory(item)}
                >
                  <div className="min-w-0">
                    <p className="text-sm text-gray-700 truncate">{item.userInput}</p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {item.targetModel} · {new Date(item.createdAt).toLocaleString()}
                    </p>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDeleteHistory(item.id); }}
                    className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 shrink-0 mt-0.5 transition-all"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
