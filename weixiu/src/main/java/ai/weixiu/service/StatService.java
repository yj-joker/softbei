package ai.weixiu.service;

import ai.weixiu.pojo.vo.AdminOverviewVO;
import ai.weixiu.pojo.vo.UserOverviewVO;

/**
 * 首页概览统计服务：仅做实时计数，不缓存、不预置数据。
 */
public interface StatService {

    /** 用户端首页概览（基于当前登录用户 BaseContext.getCurrentId()） */
    UserOverviewVO getUserOverview();

    /** 管理端首页概览（全局维度） */
    AdminOverviewVO getAdminOverview();
}
