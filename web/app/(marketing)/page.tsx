'use client';

import {
  ApartmentOutlined,
  AreaChartOutlined,
  ArrowRightOutlined,
  BookOutlined,
  CrownOutlined,
  GlobalOutlined,
  ReadOutlined,
  RobotOutlined,
  StockOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Button, Card, Col, Row, Space, Typography } from 'antd';
import Link from 'next/link';
import type { FC, ReactNode } from 'react';

import { routes } from '@/lib/constants/routes';

const { Title, Paragraph, Text } = Typography;

// Helper for consistent section styling
const SectionWrapper: FC<{ children: ReactNode; className?: string }> = ({ children, className = '' }) => (
  <section className={`relative w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 md:py-28 ${className}`}>
    {children}
  </section>
);

// Glassmorphism Card Component
const GlassCard: FC<{ icon: ReactNode; title: string; description: string }> = ({ icon, title, description }) => (
  <div className="h-full p-6 rounded-2xl bg-white/10 backdrop-blur-lg border border-white/20 transition-all duration-300 hover:border-white/40 hover:bg-white/20 transform hover:-translate-y-1">
    <div className="flex flex-col h-full">
      <div className="mb-4 text-3xl text-white/90">{icon}</div>
      <h3 className="text-xl font-bold text-white mb-2">{title}</h3>
      <p className="text-white/70 text-base leading-relaxed">{description}</p>
    </div>
  </div>
);

// --- Page Sections ---

const HeroSection: FC = () => (
  <div className="relative text-center h-[90vh] min-h-[600px] flex flex-col justify-center items-center overflow-hidden">
    <div className="z-10 px-4">
      <h1 className="text-4xl md:text-6xl font-extrabold tracking-tighter text-white drop-shadow-md mb-6 animate-fade-in-down">
        赛博投研
      </h1>
      <p className="max-w-2xl mx-auto text-lg md:text-xl text-white/80 drop-shadow-sm mb-10 animate-fade-in-up">
        AI 驱动的下一代投研平台。整合、分析、决策，赋予每位投资者机构级的智慧。
      </p>
      <div className="flex flex-col items-center gap-4 animate-fade-in-up" style={{ animationDelay: '0.5s' }}>
        <Link href={routes.aiResearcher}>
          <Button
            type="primary"
            size="large"
            className="!h-14 !px-10 !text-xl !bg-white !text-blue-900 !font-bold !border-none !shadow-lg hover:!bg-gray-200 transition-all duration-300 transform hover:scale-105"
          >
            进入工作站 <ArrowRightOutlined />
          </Button>
        </Link>
      </div>
    </div>
  </div>
);

const FeaturesSection: FC = () => {
  const features = [
    {
      icon: <RobotOutlined />,
      title: 'AI 研究员工作台',
      description: '创建专属 AI 研究员，7x24 小时监控市场，自动执行研究任务，解放生产力。',
    },
    {
      icon: <BookOutlined />,
      title: '研究文档中心',
      description: '结构化 AI 研究文档，报告自动归档、多维索引，便于追溯与复盘，构建知识资产。',
    },
    {
      icon: <ApartmentOutlined />,
      title: '任务编排与自驱',
      description: '可视化编排复杂任务流，将研究步骤自动化，实现从数据到洞察的全流程驱动。',
    },
    {
      icon: <AreaChartOutlined />,
      title: '盘前速览',
      description: '每日开盘前，自动整合宏观、市场情绪及个股动态，生成精炼决策参考。',
    },
    {
      icon: <ReadOutlined />,
      title: '资讯分析',
      description: '聚合全网海量财经资讯，AI 实时分析、摘要和情感研判，穿透市场噪音。',
    },
    {
      icon: <StockOutlined />,
      title: '模拟交易',
      description: '在与真实市场同步的环境中，无风险验证和迭代投资策略，形成投研交易闭环。',
    },
  ];

  return (
    <SectionWrapper>
      <div className="text-center mb-16">
        <h2 className="text-4xl md:text-5xl font-bold text-white">一套完整的投研操作系统</h2>
        <p className="text-lg text-white/70 mt-4 max-w-3xl mx-auto">
          从信息获取、分析研究到策略模拟，我们提供覆盖全流程的强大工具。
        </p>
      </div>
      <Row gutter={[24, 24]}>
        {features.map((feature) => (
          <Col key={feature.title} xs={24} sm={12} lg={8}>
            <GlassCard {...feature} />
          </Col>
        ))}
      </Row>
    </SectionWrapper>
  );
};

const SystemsSection: FC = () => (
  <SectionWrapper className="!py-0 md:!py-0">
    <div className="grid md:grid-cols-2 gap-10 md:gap-20 items-center">
      <div className="bg-white/5 p-8 rounded-2xl border border-white/10 backdrop-blur-sm">
        <div className="flex items-center gap-4 mb-4">
          <ThunderboltOutlined className="text-5xl text-yellow-400" />
          <h2 className="text-4xl font-bold text-white">电池系统</h2>
        </div>
        <p className="text-white/70 text-lg leading-relaxed">
          “电池”是驱动 AI 完成任务的能量。每次研究、分析或报告生成都会消耗电量。灵活的补充机制与套餐，确保您的研究引擎永不熄火。
        </p>
      </div>
      <div className="bg-white/5 p-8 rounded-2xl border border-white/10 backdrop-blur-sm">
        <div className="flex items-center gap-4 mb-4">
          <CrownOutlined className="text-5xl text-amber-400" />
          <h2 className="text-4xl font-bold text-white">会员体系</h2>
        </div>
        <p className="text-white/70 text-lg leading-relaxed">
          多层次会员服务，从免费版到旗舰版，按需解锁更强的 AI 模型、更快的分析速度和更多专属功能，最大化您的投研效率。
        </p>
      </div>
    </div>
  </SectionWrapper>
);

const SuggestionSection: FC = () => {
  const suggestions = [
    '从创建一个基础 AI 研究员开始，订阅您关注的股票或行业。',
    '尝试使用任务编排，将资讯监控、数据分析和报告生成串联。',
    '每日查看“盘前速览”，养成高效获取市场关键信息的习惯。',
    '在模拟交易中测试策略，根据 AI 报告调整，形成交易闭环。',
  ];
  return (
    <SectionWrapper>
      <div className="text-center mb-16">
        <h2 className="text-4xl md:text-5xl font-bold text-white">从新手到专家</h2>
        <p className="text-lg text-white/70 mt-4 max-w-3xl mx-auto">为您规划清晰的进阶路径，助您快速上手并充分利用平台能力。</p>
      </div>
      <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
        {suggestions.map((suggestion, index) => (
          <Card key={index} bordered={false} className="bg-white/10 !p-4 !rounded-xl border border-white/20 h-full">
            <Space align="start">
              <Text strong className="text-sky-400 text-3xl font-mono">
                {index + 1}
              </Text>
              <Paragraph className="!text-white/80 !mb-0">{suggestion}</Paragraph>
            </Space>
          </Card>
        ))}
      </div>
    </SectionWrapper>
  );
};

const CallToActionSection: FC = () => (
  <SectionWrapper>
    <div className="text-center bg-white/5 p-10 md:p-16 rounded-2xl border border-white/10 backdrop-blur-sm">
      <GlobalOutlined className="text-5xl text-white/80 mb-6" />
      <h2 className="text-4xl md:text-5xl font-bold text-white mb-4">立即开启您的 AI 投研之旅</h2>
      <p className="text-lg text-white/70 mt-4 max-w-3xl mx-auto mb-8">
        注册并免费试用，让 AI 成为您最得力的市场研究伙伴。
      </p>
      <Link href={routes.workstation}>
        <Button
          type="primary"
          size="large"
          className="!h-14 !px-8 !text-lg !bg-gradient-to-r !from-sky-500 !to-indigo-500 !text-white !font-bold !border-none !shadow-lg hover:!shadow-xl transition-all duration-300 transform hover:scale-105"
        >
          免费开始使用
        </Button>
      </Link>
    </div>
  </SectionWrapper>
);

// --- Main Page Component ---

export default function MarketingHomePage() {
  return (
    <>
      <div className="min-h-screen bg-slate-900 text-white selection:bg-sky-500/30">
        <div className="fixed inset-0 z-[-1] overflow-hidden">
          <div className="absolute top-[-20%] left-[-20%] w-[60vw] h-[60vw] bg-gradient-radial from-blue-900/40 to-transparent blur-3xl animate-blob-1"></div>
          <div className="absolute bottom-[-20%] right-[-10%] w-[50vw] h-[50vw] bg-gradient-radial from-teal-900/40 to-transparent blur-3xl animate-blob-2"></div>
          <div className="absolute top-[10%] right-[5%] w-[40vw] h-[40vw] bg-gradient-radial from-indigo-900/30 to-transparent blur-3xl animate-blob-3"></div>
        </div>

        <main>
          <HeroSection />
          <FeaturesSection />
          <SystemsSection />
          <SuggestionSection />
          <CallToActionSection />
        </main>
      </div>

      <style jsx global>{`
        @keyframes fade-in-down {
          from {
            opacity: 0;
            transform: translateY(-20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        @keyframes fade-in-up {
          from {
            opacity: 0;
            transform: translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        .animate-fade-in-down {
          animation: fade-in-down 0.8s ease-out forwards;
        }
        .animate-fade-in-up {
          animation: fade-in-up 0.8s ease-out forwards;
        }

        @keyframes blob-1-anim {
          0%,
          100% {
            transform: translate(0, 0) scale(1);
          }
          25% {
            transform: translate(20px, 30px) scale(1.05);
          }
          50% {
            transform: translate(-10px, 20px) scale(0.95);
          }
          75% {
            transform: translate(30px, -20px) scale(1.02);
          }
        }
        @keyframes blob-2-anim {
          0%,
          100% {
            transform: translate(0, 0) scale(1);
          }
          25% {
            transform: translate(-20px, -30px) scale(1.05);
          }
          50% {
            transform: translate(10px, -20px) scale(0.95);
          }
          75% {
            transform: translate(-30px, 20px) scale(1.02);
          }
        }
        @keyframes blob-3-anim {
          0%,
          100% {
            transform: translate(0, 0) scale(1);
          }
          25% {
            transform: translate(20px, -10px) scale(1.1);
          }
          50% {
            transform: translate(-10px, 20px) scale(0.9);
          }
          75% {
            transform: translate(10px, -20px) scale(1.05);
          }
        }
        .animate-blob-1 {
          animation: blob-1-anim 20s infinite ease-in-out;
        }
        .animate-blob-2 {
          animation: blob-2-anim 22s infinite ease-in-out;
          animation-delay: 3s;
        }
        .animate-blob-3 {
          animation: blob-3-anim 25s infinite ease-in-out;
          animation-delay: 5s;
        }
        .bg-gradient-radial {
          background-image: radial-gradient(circle, var(--tw-gradient-stops));
        }
      `}</style>
    </>
  );
}
