import { useQuery } from '@tanstack/react-query';

import { getHiredResearchers, getHotDocuments, getPublicRank, getWorkbenchOverview } from '../api';
import { RankSortBy } from '@/types/researcher-workbench';

const featureKey = 'researcher-workbench';

export const useWorkbenchOverview = (sortBy: RankSortBy = 'today', enabled: boolean = true) => {
  return useQuery({
    queryKey: [featureKey, 'overview', sortBy],
    queryFn: () => getWorkbenchOverview(sortBy),
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
};

export const useHiredResearchers = (enabled: boolean = true) => {
  return useQuery({
    queryKey: [featureKey, 'hired'],
    queryFn: getHiredResearchers,
    enabled,
    staleTime: 30_000,               // 30秒内复用缓存，不重复请求
    refetchOnWindowFocus: false,
  });
};

export const useHotDocuments = (enabled: boolean = true) => {
  return useQuery({
    queryKey: [featureKey, 'hot-documents'],
    queryFn: getHotDocuments,
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
};

export const usePublicRank = (sortBy: RankSortBy, enabled: boolean = true) => {
  return useQuery({
    queryKey: [featureKey, 'public-rank', sortBy],
    queryFn: () => getPublicRank(sortBy),
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
};
