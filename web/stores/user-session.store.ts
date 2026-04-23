/**
 * 用户会话全局状态（Zustand）
 *
 * 功能：
 *  - 存储 accessToken 和用户信息（UserProfile）
 *  - token 持久化到 localStorage
 *  - 提供 login / logout / hydrate 方法
 *  - hydrate 在应用启动时从 localStorage 恢复会话
 */
import { create } from 'zustand';

import type { UserProfile } from '@/features/auth/api';

interface UserSessionState {
  /** JWT 访问令牌 */
  accessToken: string | null;
  /** 当前登录用户信息 */
  user: UserProfile | null;
  /** 是否已从 localStorage 恢复 */
  hydrated: boolean;

  /** 登录成功后调用，保存 token + 用户信息到 store 和 localStorage */
  login: (token: string, user: UserProfile) => void;
  /** 更新用户信息（不改 token） */
  setUser: (user: UserProfile) => void;
  /** 退出登录，清除 store 和 localStorage */
  logout: () => void;
  /** 应用启动时从 localStorage 恢复 token */
  hydrate: () => void;
}

export const useUserSessionStore = create<UserSessionState>((set) => ({
  accessToken: null,
  user: null,
  hydrated: false,

  login: (token, user) => {
    localStorage.setItem('access_token', token);
    localStorage.setItem('user_profile', JSON.stringify(user));
    set({ accessToken: token, user, hydrated: true });
  },

  setUser: (user) => {
    localStorage.setItem('user_profile', JSON.stringify(user));
    set({ user });
  },

  logout: () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_profile');
    set({ accessToken: null, user: null, hydrated: true });
  },

  hydrate: () => {
    const token = localStorage.getItem('access_token');
    const raw = localStorage.getItem('user_profile');
    let user: UserProfile | null = null;
    if (raw) {
      try { user = JSON.parse(raw) as UserProfile; } catch { /* 忽略解析失败 */ }
    }
    set({ accessToken: token, user, hydrated: true });
  },
}));
