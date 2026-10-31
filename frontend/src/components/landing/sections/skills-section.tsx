import ProgressiveSkillsAnimation from "../progressive-skills-animation";
import { Section } from "../section";

export function SkillsSection({ className }: { className?: string }) {
  return (
    <Section
      id="skills"
      // Pinned to the viewport only where the two-pane animation fits; on
      // phones it stacks and needs to grow to its own height instead.
      className={className}
      kicker="Agent skills"
      title={<>Agent skills and security playbooks</>}
      subtitle="Skills load progressively. Nova pulls in only the playbook a task needs, when it needs it, and every skill is a plain file you can read, edit and ship with your own tools."
    >
      <div className="relative overflow-hidden">
        <ProgressiveSkillsAnimation />
      </div>
    </Section>
  );
}
