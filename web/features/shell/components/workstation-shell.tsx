/**
 * 工作台外壳 Shell
 *
 * 包含：
 *  - 左侧边栏（桌面端固定 / 移动端 Drawer 抽屉）
 *  - 顶部导航栏（汉堡菜单 + 产品介绍/工作台/使用说明 + 搜索/通知/VIP/电池/头像下拉）
 *  - 内容区域
 *
 * 响应式：
 *  - md 以下：侧边栏隐藏，通过汉堡菜单打开 Drawer
 *  - md 以上：侧边栏固定，支持折叠
 */
'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import type { Route } from 'next';
import { usePathname, useRouter } from 'next/navigation';
import {
  Avatar,
  Badge,
  Button,
  Drawer,
  Dropdown,
  Layout,
  Menu,
  Space,
  Tooltip,
} from 'antd';
import {
  BellOutlined,
  CloseOutlined,
  CrownOutlined,
  LeftOutlined,
  LoginOutlined,
  LogoutOutlined,
  MenuOutlined,
  RightOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import type { PropsWithChildren } from 'react';

import { workstationNav, type NavItem } from '@/features/navigation/config/workstation-nav';
import { routes } from '@/lib/constants/routes';
import { useAppShellStore } from '@/stores/app-shell.store';
import { useUserSessionStore } from '@/stores/user-session.store';

const { Header, Sider, Content } = Layout;

function buildMenuItems(items: NavItem[]): MenuProps['items'] {
  return items.map((item) => {
    if (item.children) {
      return {
        key: item.key,
        icon: <item.icon />,
        label: item.label,
        children: buildMenuItems(item.children),
      };
    }
    return {
      key: item.key,
      icon: <item.icon />,
      label: item.href ? <Link href={item.href as Route}>{item.label}</Link> : item.label,
    };
  });
}

function findSelectedKeys(pathname: string, items: NavItem[]): string[] {
  for (const item of items) {
    if (item.href && pathname.startsWith(item.href)) {
      return [item.key];
    }
    if (item.children) {
      const found = findSelectedKeys(pathname, item.children);
      if (found.length) return found;
    }
  }
  return [];
}

function findOpenKeys(pathname: string, items: NavItem[]): string[] {
  for (const item of items) {
    if (item.children) {
      const found = findSelectedKeys(pathname, item.children);
      if (found.length) return [item.key];
    }
  }
  return [];
}

export function WorkstationShell({ children }: PropsWithChildren) {
  const collapsed = useAppShellStore((s) => s.collapsed);
  const toggleCollapsed = useAppShellStore((s) => s.toggleCollapsed);
  const pathname = usePathname();
  const router = useRouter();

  // 移动端抽屉状态
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 用户会话
  const accessToken = useUserSessionStore((s) => s.accessToken);
  const user = useUserSessionStore((s) => s.user);
  const hydrated = useUserSessionStore((s) => s.hydrated);
  const hydrate = useUserSessionStore((s) => s.hydrate);
  const logout = useUserSessionStore((s) => s.logout);

  // 应用启动时从 localStorage 恢复登录态
  useEffect(() => {
    if (!hydrated) hydrate();
  }, [hydrated, hydrate]);

  // 登录态恢复完成后，若仍无 token，直接回登录页。
  useEffect(() => {
    if (!hydrated) return;
    if (accessToken) return;
    router.replace(routes.login);
  }, [hydrated, accessToken, router]);

  // 路由切换时自动关闭移动端抽屉
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  const selectedKeys = findSelectedKeys(pathname, workstationNav);
  const defaultOpenKeys = findOpenKeys(pathname, workstationNav);

  /** 头像下拉菜单 */
  const avatarMenuItems: MenuProps['items'] = user
    ? [
        {
          key: 'profile',
          icon: <UserOutlined />,
          label: user.nickname,
          disabled: true,
          className: '!cursor-default',
        },
        { type: 'divider' },
        {
          key: 'account',
          icon: <SettingOutlined />,
          label: '账户中心',
          onClick: () => router.push(routes.billing),
        },
        {
          key: 'plans',
          icon: <CrownOutlined />,
          label: '会员套餐',
          onClick: () => router.push(routes.billing),
        },
        { type: 'divider' },
        {
          key: 'logout',
          icon: <LogoutOutlined />,
          label: '退出登录',
          danger: true,
          onClick: () => {
            logout();
            router.push(routes.login);
          },
        },
      ]
    : [
        {
          key: 'login',
          icon: <LoginOutlined />,
          label: '登录 / 注册',
          onClick: () => router.push(routes.login),
        },
      ];

  /** 侧边栏菜单内容（桌面端和移动端共用） */
  const sidebarContent = (
    <div className="flex h-full flex-col">
      {/* Logo */}
      <div className="flex items-center gap-2 px-5 py-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-500 text-sm font-bold text-white">
          赛
        </div>
        <span className="text-base font-semibold text-slate-800">赛博投研</span>
      </div>
      {/* Nav */}
      <div className="flex-1 overflow-y-auto">
        <Menu
          mode="inline"
          selectedKeys={selectedKeys}
          defaultOpenKeys={defaultOpenKeys}
          items={buildMenuItems(workstationNav)}
          className="!border-r-0"
        />
      </div>
    </div>
  );

  // 先等待 localStorage 中的 token 恢复，避免子页面在空鉴权状态下抢跑请求。
  if (!hydrated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <Space direction="vertical" size={8} align="center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-brand-500" />
          <span className="text-sm text-slate-400">正在恢复登录态...</span>
        </Space>
      </div>
    );
  }

  if (!accessToken) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <Space direction="vertical" size={8} align="center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-brand-500" />
          <span className="text-sm text-slate-400">正在跳转登录页...</span>
        </Space>
      </div>
    );
  }

  return (
    <Layout className="min-h-screen">
      {/* ── 桌面端固定侧边栏（md 以上） ── */}
      <Sider
        width={220}
        collapsedWidth={64}
        collapsed={collapsed}
        className="!fixed !left-0 !top-0 !bottom-0 !z-20 !bg-white border-r border-slate-200 max-md:!hidden"
        trigger={null}
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center gap-2 px-5 py-4">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-500 text-sm font-bold text-white">
              赛
            </div>
            {!collapsed && (
              <span className="text-base font-semibold text-slate-800">赛博投研</span>
            )}
          </div>
          <div className="flex-1 overflow-y-auto">
            <Menu
              mode="inline"
              selectedKeys={selectedKeys}
              defaultOpenKeys={defaultOpenKeys}
              items={buildMenuItems(workstationNav)}
              className="!border-r-0"
            />
          </div>
          {/* 折叠按钮 */}
          <button
            onClick={toggleCollapsed}
            className="absolute -right-3 top-1/2 z-30 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-400 shadow-sm hover:text-brand-500 transition-colors"
          >
            {collapsed ? <RightOutlined style={{ fontSize: 10 }} /> : <LeftOutlined style={{ fontSize: 10 }} />}
          </button>
        </div>
      </Sider>

      {/* ── 移动端 Drawer 侧边栏（md 以下） ── */}
      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        placement="left"
        closable={false}
        className="md:!hidden"
        styles={{ wrapper: { width: 260 }, body: { padding: 0 } }}
      >
        {sidebarContent}
      </Drawer>

      {/* ── Main ── */}
      <Layout className="transition-all duration-200 md:ml-[var(--sidebar-w)]" style={{ '--sidebar-w': collapsed ? '64px' : '220px' } as React.CSSProperties}>
        {/* 顶部导航 */}
        <Header className="!sticky !top-0 !z-10 flex items-center justify-between border-b border-slate-200 !bg-white !px-3 sm:!px-6 !h-14 !leading-[56px]">
          {/* 左侧：移动端汉堡菜单 */}
          <div className="flex flex-1 items-center gap-2">
            <Button
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setDrawerOpen(true)}
              className="md:!hidden !text-slate-600"
            />
          </div>

          {/* 中间导航 —— 小屏隐藏，根据当前路由动态高亮 */}
          <Space size={24} className="hidden sm:flex">
            {[
              { label: '产品介绍', href: '/', match: (p: string) => p === '/' },
              { label: '工作台', href: '/workstation', match: (p: string) => p.startsWith('/workstation') && !p.startsWith(routes.userGuide) },
              { label: '使用说明', href: routes.userGuide, match: (p: string) => p.startsWith(routes.userGuide) },
            ].map((item) => {
              const active = item.match(pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href as Route}
                  className={`text-sm transition-colors ${
                    active ? 'font-medium text-brand-500' : 'text-slate-600 hover:text-brand-500'
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </Space>

          {/* 右侧工具区 */}
          <div className="flex flex-1 items-center justify-end gap-2 sm:gap-3">
            <Tooltip title="搜索">
              <Button type="text" icon={<SearchOutlined />} className="!text-slate-500" size="small" />
            </Tooltip>
            <Tooltip title="通知">
              <Badge dot>
                <Button type="text" icon={<BellOutlined />} className="!text-slate-500" size="small" />
              </Badge>
            </Tooltip>
            {/* VIP 徽章 —— 极小屏隐藏 */}
            <Link href={routes.billing} className="hidden xs:block">
              <div className="flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 sm:px-3 sm:py-1 cursor-pointer hover:bg-brand-100 transition-colors">
                <CrownOutlined className="text-brand-500 text-xs" />
                <span className="text-xs font-medium text-brand-600 hidden sm:inline">
                  {user ? user.membership_level : '开通VIP'}
                </span>
              </div>
            </Link>
            {/* 电池余额 —— 极小屏隐藏 */}
            <div className="hidden sm:flex items-center gap-1 text-sm text-slate-500">
              <ThunderboltOutlined className="text-amber-500" />
              <span>{user?.battery_balance ?? 0}</span>
            </div>
            <Dropdown menu={{ items: avatarMenuItems }} trigger={['click']} placement="bottomRight">
              <Avatar size={30} icon={<UserOutlined />} className="cursor-pointer bg-brand-400 shrink-0" />
            </Dropdown>
          </div>
        </Header>

        {/* 页面内容 */}
        <Content className="p-3 sm:p-5 bg-[#f5f7fb] min-h-[calc(100vh-56px)]">{children}</Content>
      </Layout>
    </Layout>
  );
}
