"use client";

import { useState } from "react";
import { Save, ChevronDown, ChevronUp } from "lucide-react";
import { api, type Project } from "@/lib/api";

interface ContextEditorProps {
  project: Project;
  onSaved: (updated: Project) => void;
}

export default function ContextEditor({ project, onSaved }: ContextEditorProps) {
  const [open, setOpen] = useState(true);
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [context, setContext] = useState(project.context);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const dirty =
    name !== project.name ||
    description !== project.description ||
    context !== project.context;

  async function handleSave() {
    if (!dirty) return;
    setSaving(true);
    try {
      const updated = await api.projects.update(project.id, { name, description, context });
      onSaved(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-50 transition-colors"
      >
        <div>
          <h2 className="text-sm font-semibold text-gray-700 text-left">Project Context</h2>
          <p className="text-xs text-gray-400 mt-0.5 text-left">
            This context is injected into every prompt optimization
          </p>
        </div>
        {open ? <ChevronUp size={16} className="text-gray-400" /> : <ChevronDown size={16} className="text-gray-400" />}
      </button>

      {open && (
        <div className="px-5 pb-5 space-y-4 border-t border-gray-100">
          <div className="grid grid-cols-2 gap-3 pt-4">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Project Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full text-sm px-3 py-2 rounded-lg border border-gray-200 focus:border-brand-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Short Description</label>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="e.g. Marketing content for SaaS"
                className="w-full text-sm px-3 py-2 rounded-lg border border-gray-200 focus:border-brand-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Context Brain
              <span className="ml-1 font-normal text-gray-400">— persona, tone, audience, domain knowledge</span>
            </label>
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={6}
              placeholder={`Example:\nI am a Traditional Chinese Medicine practitioner specializing in acupuncture.\nTarget audience: athletes aged 30-55.\nTone: professional, scientific yet accessible, calming and empathetic.`}
              className="w-full text-sm px-3 py-2 rounded-lg border border-gray-200 focus:border-brand-500 focus:outline-none resize-none font-mono leading-relaxed"
            />
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleSave}
              disabled={!dirty || saving}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-brand-500 text-white hover:bg-brand-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Save size={14} />
              {saving ? "Saving…" : saved ? "Saved!" : "Save Context"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
