"use client";

import {
  Folder,
  FileText,
  Search,
  Globe,
  Check,
  Sparkles,
  Terminal,
  Play,
  Pause,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { useState, useEffect, useRef } from "react";

import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";

type AnimationPhase =
  | "idle"
  | "user-input"
  | "scanning"
  | "load-skill"
  | "load-playbook"
  | "scan-repo"
  | "load-hardening"
  | "fixing"
  | "load-ship"
  | "verifying"
  | "done";

interface FileItem {
  name: string;
  type: "folder" | "file";
  indent: number;
  highlight?: boolean;
  active?: boolean;
  done?: boolean;
  dragging?: boolean;
}

/** What the security scan looks like while it runs. */
const scanSteps = [
  { type: "search", text: "secrets scan: config/*.py" },
  { type: "fetch", text: "Found a leaked secret in config.py" },
  { type: "search", text: "dependency audit: 214 packages" },
  { type: "search", text: "secure-ship gate: 0 critical, 0 high" },
  { type: "fetch", text: "policy: block on critical or high" },
];

/** The files the agent rewrites once the leak is found. */
const patchedFiles = ["config.py", "config.py (rotated)", "package.json"];

// Animation duration configuration - adjust the duration for each step here
const ANIMATION_DELAYS = {
  "user-input": 0, // User input phase duration (milliseconds)
  scanning: 2000, // Scanning phase duration
  "load-skill": 1500, // Load skill phase duration
  "load-playbook": 1200, // Load playbook phase duration
  "scan-repo": 800, // Running the scans
  "load-hardening": 800, // Load the hardening skill
  fixing: 1200, // Fixing what was found
  "load-ship": 2500, // Load the ship gate
  verifying: 1200, // Re-checking
  done: 2500, // Done phase duration (final step)
} as const;

export default function ProgressiveSkillsAnimation() {
  const { t } = useI18n();
  const [phase, setPhase] = useState<AnimationPhase>("idle");
  const [scanIndex, setScanIndex] = useState(0);
  const [patchIndex, setPatchIndex] = useState(0);
  const [, setChatMessages] = useState<React.ReactNode[]>([]);
  const [, setShowWorkspace] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(false);
  const [hasAutoPlayed, setHasAutoPlayed] = useState(false);
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const timeoutsRef = useRef<NodeJS.Timeout[]>([]);

  // Additional display duration after the final step (done) completes, used to show the final result
  const FINAL_DISPLAY_DURATION = 3000; // milliseconds

  // Play animation only when isPlaying is true
  useEffect(() => {
    if (!isPlaying) {
      // Clear all timeouts when paused
      timeoutsRef.current.forEach(clearTimeout);
      timeoutsRef.current = [];
      return;
    }

    const timeline = [
      { phase: "user-input" as const, delay: ANIMATION_DELAYS["user-input"] },
      { phase: "scanning" as const, delay: ANIMATION_DELAYS.scanning },
      { phase: "load-skill" as const, delay: ANIMATION_DELAYS["load-skill"] },
      {
        phase: "load-playbook" as const,
        delay: ANIMATION_DELAYS["load-playbook"],
      },
      { phase: "scan-repo" as const, delay: ANIMATION_DELAYS["scan-repo"] },
      {
        phase: "load-hardening" as const,
        delay: ANIMATION_DELAYS["load-hardening"],
      },
      { phase: "fixing" as const, delay: ANIMATION_DELAYS.fixing },
      { phase: "load-ship" as const, delay: ANIMATION_DELAYS["load-ship"] },
      { phase: "verifying" as const, delay: ANIMATION_DELAYS.verifying },
      { phase: "done" as const, delay: ANIMATION_DELAYS.done },
    ];

    let totalDelay = 0;
    const timeouts: NodeJS.Timeout[] = [];

    timeline.forEach(({ phase, delay }) => {
      totalDelay += delay;
      timeouts.push(setTimeout(() => setPhase(phase), totalDelay));
    });

    // Reset after animation completes
    // Total duration for the final step = ANIMATION_DELAYS["done"] + FINAL_DISPLAY_DURATION
    timeouts.push(
      setTimeout(() => {
        setPhase("idle");
        setChatMessages([]);
        setScanIndex(0);
        setPatchIndex(0);
        setShowWorkspace(false);
        setIsPlaying(false);
      }, totalDelay + FINAL_DISPLAY_DURATION),
    );

    timeoutsRef.current = timeouts;

    return () => {
      timeouts.forEach(clearTimeout);
      timeoutsRef.current = [];
    };
  }, [isPlaying]);

  const handlePlay = () => {
    setIsPlaying(true);
    setHasPlayed(true);
    setPhase("idle");
    setChatMessages([]);
    setScanIndex(0);
    setPatchIndex(0);
    setShowWorkspace(false);
  };

  const handleTogglePlayPause = () => {
    if (isPlaying) {
      setIsPlaying(false);
    } else {
      // If animation hasn't started or is at idle, restart from beginning
      if (phase === "idle") {
        handlePlay();
      } else {
        // Resume from current phase
        setIsPlaying(true);
      }
    }
  };

  // Auto-play when component enters viewport for the first time
  useEffect(() => {
    if (hasAutoPlayed || !containerRef.current) return;

    const containerElement = containerRef.current;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !hasAutoPlayed && !isPlaying) {
            setHasAutoPlayed(true);
            // Small delay before auto-playing for better UX
            setTimeout(() => {
              setIsPlaying(true);
              setHasPlayed(true);
              setPhase("idle");
              setChatMessages([]);
              setScanIndex(0);
              setPatchIndex(0);
              setShowWorkspace(false);
            }, 300);
          }
        });
      },
      {
        threshold: 0.3, // Trigger when 30% of the component is visible
        rootMargin: "0px",
      },
    );

    observer.observe(containerElement);

    return () => {
      if (containerElement) {
        observer.unobserve(containerElement);
      }
    };
  }, [hasAutoPlayed, isPlaying]);

  // Handle the scan animation
  useEffect(() => {
    if (phase === "scan-repo" && scanIndex < scanSteps.length) {
      const timer = setTimeout(() => {
        setScanIndex((i) => i + 1);
      }, 350);
      return () => clearTimeout(timer);
    }
  }, [phase, scanIndex]);

  // Handle the fix animation
  useEffect(() => {
    if (phase === "fixing" && patchIndex < patchedFiles.length) {
      const timer = setTimeout(() => {
        setPatchIndex((i) => i + 1);
      }, 600);
      return () => clearTimeout(timer);
    }
    if (phase === "fixing") {
      setShowWorkspace(true);
    }
  }, [phase, patchIndex]);

  // Auto scroll chat to bottom when messages change
  useEffect(() => {
    if (chatMessagesRef.current && phase !== "idle") {
      chatMessagesRef.current.scrollTo({
        top: chatMessagesRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [phase, scanIndex, patchIndex]);

  const getFileTree = (): FileItem[] => {
    const base: FileItem[] = [
      {
        name: "security-audit",
        type: "folder",
        indent: 0,
        highlight: phase === "scanning",
        active: ["load-skill", "load-playbook", "scan-repo"].includes(phase),
        done: [
          "scan-repo",
          "load-hardening",
          "fixing",
          "load-ship",
          "verifying",
          "done",
        ].includes(phase),
      },
      {
        name: "SKILL.md",
        type: "file",
        indent: 1,
        highlight: phase === "scanning",
        dragging: phase === "load-skill",
        done: [
          "load-playbook",
          "scan-repo",
          "load-hardening",
          "fixing",
          "load-ship",
          "verifying",
          "done",
        ].includes(phase),
      },
      {
        name: "webapp-testing",
        type: "folder",
        indent: 0,
        highlight: phase === "scanning",
        active: ["load-playbook", "scan-repo"].includes(phase),
        done: [
          "scan-repo",
          "load-hardening",
          "fixing",
          "load-ship",
          "verifying",
          "done",
        ].includes(phase),
      },
      {
        name: "SKILL.md",
        type: "file",
        indent: 1,
        highlight: phase === "scanning",
        dragging: phase === "load-playbook",
        done: [
          "scan-repo",
          "load-hardening",
          "fixing",
          "load-ship",
          "verifying",
          "done",
        ].includes(phase),
      },
      {
        name: "secrets-scan",
        type: "folder",
        indent: 0,
        highlight: phase === "scanning",
        active: ["load-ship", "verifying"].includes(phase),
        done: ["verifying", "done"].includes(phase),
      },
      {
        name: "SKILL.md",
        type: "file",
        indent: 1,
        highlight: phase === "scanning",
        dragging: phase === "load-ship",
        done: ["verifying", "done"].includes(phase),
      },
    ];

    // The hardening skill is pulled in only once the leak is found.
    if (phase !== "idle" && phase !== "user-input" && phase !== "scanning") {
      base.push(
        {
          name: "hardening",
          type: "folder",
          indent: 0,
          active: ["load-hardening", "fixing"].includes(phase),
          done: ["fixing", "load-ship", "verifying", "done"].includes(phase),
        },
        {
          name: "SKILL.md",
          type: "file",
          indent: 1,
          dragging: phase === "load-hardening",
          done: ["fixing", "load-ship", "verifying", "done"].includes(phase),
        },
      );
    }

    base.push(
      {
        name: "scripts",
        type: "folder",
        indent: 0,
        done: ["verifying", "done"].includes(phase),
      },
      {
        name: "scan.sh",
        type: "file",
        indent: 1,
        done: ["verifying", "done"].includes(phase),
      },
    );

    return base;
  };

  return (
    <div
      ref={containerRef}
      className="relative flex w-full items-center justify-center overflow-hidden p-4 md:h-[calc(100vh-280px)] md:p-8"
    >
      {/* Overlay and Play Button */}
      <AnimatePresence>
        {!isPlaying && !hasPlayed && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-50 flex items-center justify-center"
          >
            <motion.button
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.8, opacity: 0 }}
              onClick={handlePlay}
              className="group flex flex-col items-center gap-4 transition-transform hover:scale-105 active:scale-95"
            >
              <div className="flex h-24 w-24 items-center justify-center rounded-full bg-white/10 backdrop-blur-md transition-all group-hover:bg-white/20">
                <Play
                  size={48}
                  className="ml-1 text-white transition-transform group-hover:scale-110"
                  fill="white"
                />
              </div>
              <span className="text-lg font-medium text-white">
                Click to play
              </span>
            </motion.button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Bottom Left Play/Pause Button */}
      <Tooltip content="Play / Pause">
        <div className="absolute bottom-4 left-4 z-40 flex items-center gap-2 md:bottom-12 md:left-12">
          <motion.button
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            onClick={handleTogglePlayPause}
            className="flex h-12 w-12 items-center justify-center rounded-full bg-white/10 backdrop-blur-md transition-all hover:scale-110 hover:bg-white/20 active:scale-95"
          >
            {isPlaying ? (
              <Pause size={24} className="text-white" fill="white" />
            ) : (
              <Play size={24} className="ml-0.5 text-white" fill="white" />
            )}
          </motion.button>
          <span className="text-lg font-medium">
            Click to {isPlaying ? "pause" : "play"}
          </span>
        </div>
      </Tooltip>

      {/* Two panes side by side would be unreadably narrow on a phone. */}
      <div className="flex h-full max-h-[700px] w-full max-w-6xl flex-col gap-4 md:flex-row md:gap-8">
        {/* Left: File Tree */}
        <div className="flex flex-1 flex-col">
          <motion.div
            className="mb-4 font-mono text-sm text-slate-500"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            /mnt/skills/
          </motion.div>

          <div className="space-y-2">
            {getFileTree().map((item, index) => (
              <motion.div
                key={`${item.name}-${index}`}
                className={`flex items-center gap-3 text-lg font-medium transition-all duration-300 ${
                  item.done
                    ? "text-[#34d399]"
                    : item.dragging
                      ? "translate-x-8 scale-105 text-[#22d3ee]"
                      : item.active
                        ? "text-white"
                        : item.highlight
                          ? "text-[#a78bfa]"
                          : "text-slate-600"
                }`}
                style={{ paddingLeft: `${item.indent * 24}px` }}
                animate={
                  item.done
                    ? {
                        scale: 1,
                        opacity: 1,
                      }
                    : {}
                }
              >
                {item.type === "folder" ? (
                  <Folder
                    size={20}
                    className={
                      item.done
                        ? "text-[#34d399]"
                        : item.highlight
                          ? "text-[#a78bfa]"
                          : ""
                    }
                  />
                ) : (
                  <FileText
                    size={20}
                    className={
                      item.done
                        ? "text-[#34d399]"
                        : item.highlight
                          ? "text-[#a78bfa]"
                          : ""
                    }
                  />
                )}
                <span>{item.name}</span>
                {item.done && <Check size={16} className="text-[#34d399]" />}
                {item.highlight && !item.done && (
                  <Sparkles size={16} className="text-[#a78bfa]" />
                )}
              </motion.div>
            ))}
          </div>
        </div>

        {/* Right: Chat Interface */}
        <div className="flex flex-1 flex-col overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03]">
          {/* Chat Header */}
          <div className="border-b border-white/10 p-4">
            <div className="flex items-center gap-2">
              <div className="h-3 w-3 rounded-full bg-[#34d399]" />
              <span className="text-sm text-slate-400">
                {t.landing.skillsAnimation.agentLabel}
              </span>
            </div>
          </div>

          {/* Chat Messages */}
          <div
            ref={chatMessagesRef}
            className="flex-1 space-y-4 overflow-y-auto p-6"
          >
            {/* User Message */}
            <AnimatePresence>
              {phase !== "idle" && (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-end"
                >
                  <div className="max-w-[90%] rounded-2xl rounded-tr-sm border border-[#926dff]/30 bg-[#926dff]/15 px-5 py-3">
                    <p className="text-base">
                      Audit the customer portal for leaked secrets, fix what you
                      find and ship a verified build
                    </p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Agent Messages */}
            <AnimatePresence>
              {phase !== "idle" && phase !== "user-input" && (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="space-y-3"
                >
                  {/* Found Skills */}
                  {[
                    "scanning",
                    "load-skill",
                    "load-playbook",
                    "scan-repo",
                    "load-hardening",
                    "fixing",
                    "load-ship",
                    "verifying",
                    "done",
                  ].includes(phase) && (
                    <div className="text-base text-slate-300">
                      <span className="text-[#a78bfa]">✨</span> Found 3 skills
                    </div>
                  )}

                  {/* Scanning */}
                  {[
                    "load-skill",
                    "load-playbook",
                    "scan-repo",
                    "load-hardening",
                    "fixing",
                    "load-ship",
                    "verifying",
                    "done",
                  ].includes(phase) && (
                    <div className="mt-4">
                      <hr className="mb-3 border-white/10" />
                      <div className="mb-3 text-slate-300">🔍 Scanning…</div>
                      <div className="mb-3 space-y-2">
                        {/* Loading security-audit */}
                        {[
                          "load-skill",
                          "load-playbook",
                          "scan-repo",
                          "load-hardening",
                          "fixing",
                          "load-ship",
                          "verifying",
                          "done",
                        ].includes(phase) && (
                          <div className="flex items-center gap-2 pl-4 text-slate-400">
                            <FileText size={16} />
                            <span>
                              {t.landing.skillsAnimation.loadingSkill(
                                "security-audit",
                              )}
                            </span>
                          </div>
                        )}
                        {/* Loading webapp-testing */}
                        {[
                          "load-playbook",
                          "scan-repo",
                          "load-hardening",
                          "fixing",
                          "load-ship",
                          "verifying",
                          "done",
                        ].includes(phase) && (
                          <div className="flex items-center gap-2 pl-4 text-slate-400">
                            <FileText size={16} />
                            <span>
                              {t.landing.skillsAnimation.loadingSkill(
                                "webapp-testing",
                              )}
                            </span>
                          </div>
                        )}
                      </div>
                      {/* Scan steps */}
                      {phase === "scan-repo" && (
                        <div className="max-h-[180px] space-y-2 overflow-hidden pl-4">
                          {scanSteps.slice(0, scanIndex).map((step, i) => (
                            <motion.div
                              key={i}
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              className="flex items-center gap-2 text-sm text-slate-500"
                            >
                              {step.type === "search" ? (
                                <Search size={14} className="text-[#22d3ee]" />
                              ) : (
                                <Globe size={14} className="text-[#fbbf24]" />
                              )}
                              <span className="truncate">{step.text}</span>
                            </motion.div>
                          ))}
                        </div>
                      )}
                      {[
                        "load-hardening",
                        "fixing",
                        "load-ship",
                        "verifying",
                        "done",
                      ].includes(phase) && (
                        <div className="max-h-[180px] space-y-2 overflow-hidden pl-4">
                          {scanSteps.map((step, i) => (
                            <motion.div
                              key={i}
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              className="flex items-center gap-2 text-sm text-slate-500"
                            >
                              {step.type === "search" ? (
                                <Search size={14} className="text-[#22d3ee]" />
                              ) : (
                                <Globe size={14} className="text-[#fbbf24]" />
                              )}
                              <span className="truncate">{step.text}</span>
                            </motion.div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Fixing */}
                  {["fixing", "load-ship", "verifying", "done"].includes(
                    phase,
                  ) && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="mt-4"
                    >
                      <hr className="mb-3 border-white/10" />
                      <div className="mb-3 text-slate-300">🔧 Fixing…</div>
                      <div className="mb-3 flex items-center gap-2 pl-4 text-slate-400">
                        <FileText size={16} />
                        <span>
                          {t.landing.skillsAnimation.loadingSkill("hardening")}
                        </span>
                      </div>
                      <div className="space-y-2 pl-4">
                        {patchedFiles.slice(0, patchIndex).map((file) => (
                          <motion.div
                            key={file}
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="flex items-center gap-2 text-sm text-[#34d399]"
                          >
                            <FileText size={14} />
                            <span>
                              {t.landing.skillsAnimation.generating(file)}
                            </span>
                            <Check size={14} />
                          </motion.div>
                        ))}
                      </div>
                    </motion.div>
                  )}

                  {/* Shipping */}
                  {["load-ship", "verifying", "done"].includes(phase) && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="mt-4"
                    >
                      <hr className="mb-3 border-white/10" />
                      <div className="mb-3 text-slate-300">
                        {phase === "verifying"
                          ? "🔁 Re-checking…"
                          : "📦 Preparing the build…"}
                      </div>
                      <div className="mb-3 space-y-2">
                        <div className="flex items-center gap-2 pl-4 text-slate-400">
                          <FileText size={16} />
                          <span>
                            {t.landing.skillsAnimation.loadingSkill(
                              "secrets-scan",
                            )}
                          </span>
                        </div>
                        {["verifying", "done"].includes(phase) && (
                          <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="flex items-center gap-2 pl-4 text-slate-400"
                          >
                            <Terminal size={16} />
                            <span>
                              {t.landing.skillsAnimation.executing(
                                "scripts/scan.sh",
                              )}
                            </span>
                          </motion.div>
                        )}
                      </div>
                      {phase === "done" && (
                        <motion.div
                          initial={{ opacity: 0, scale: 0.9 }}
                          animate={{ opacity: 1, scale: 1 }}
                          className="mt-4 rounded-xl border border-[#34d399]/30 bg-[#34d399]/10 p-4"
                        >
                          <div className="text-lg font-medium text-[#34d399]">
                            ✅ Ready to ship — 0 critical, 0 high
                          </div>
                        </motion.div>
                      )}
                    </motion.div>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Chat Input (decorative) */}
          <div className="border-t border-white/10 p-4">
            <div className="rounded-xl bg-white/[0.04] px-4 py-3 text-sm text-slate-500">
              Ask Nova anything...
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
