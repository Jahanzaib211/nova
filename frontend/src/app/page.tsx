import { Footer } from "@/components/landing/footer";
import { Header } from "@/components/landing/header";
import { Hero } from "@/components/landing/hero";
import { CaseStudySection } from "@/components/landing/sections/case-study-section";
import { CommunitySection } from "@/components/landing/sections/community-section";
import { LoopSection } from "@/components/landing/sections/loop-section";
import { PricingTeaserSection } from "@/components/landing/sections/pricing-teaser-section";
import { ProblemSection } from "@/components/landing/sections/problem-section";
import { ProductSection } from "@/components/landing/sections/product-section";
import { ProofSection } from "@/components/landing/sections/proof-section";
import { SecureVibeSection } from "@/components/landing/sections/secure-vibe-section";
import { SecuritySection } from "@/components/landing/sections/security-section";
import { SkillsSection } from "@/components/landing/sections/skills-section";
import { WhatsNewSection } from "@/components/landing/sections/whats-new-section";

export default function LandingPage() {
  return (
    <div className="min-h-screen w-full bg-[#0a0a0a]">
      <Header homeURL="/" className="text-white" tone="dark" nav="landing" />
      <main className="flex w-full flex-col">
        <Hero />
        <ProblemSection />
        <ProductSection />
        <LoopSection />
        <SecuritySection />
        <SecureVibeSection />
        <SkillsSection />
        <CaseStudySection />
        <WhatsNewSection />
        <ProofSection />
        <PricingTeaserSection />
        <CommunitySection />
      </main>
      <Footer />
    </div>
  );
}
