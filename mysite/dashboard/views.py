from django.shortcuts import render, redirect
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required, user_passes_test
from social_django.utils import load_strategy, load_backend
from django.http import HttpResponse, JsonResponse
from social_core.actions import do_auth
from trade.utils import get_monthly_rotating_logger, get_all_future_open_order, get_strategy_by_symbol, grid_v2_lab_2, get_active_grid_v2_symbols
from trade.utils import get_main_account_info
from binance import Client
from django.views.decorators.http import require_POST
import os
from trade.models import Strategy

logger = get_monthly_rotating_logger('dashboard', '../logs')

# 檢查是否為超級使用者的裝飾器
def superuser_required(view_func):
    decorated_view = user_passes_test(lambda u: u.is_superuser)(view_func)
    return login_required(decorated_view)

def login_view(request):
    try:
        strategy = load_strategy(request)
        backend = load_backend(strategy, 'google-oauth2', None)
        logger.info(f"Backend loaded: {backend}")
        
        if request.user.is_authenticated:
            return redirect('dashboard:index')
        return render(request, 'dashboard/login.html')
    except Exception as e:
        logger.warning(f"Login error: {str(e)}")
        return render(request, 'dashboard/error.html', {'error': str(e)})

@login_required
def logout_view(request):
    logger.info(f"User logged out: {request.user.email}")
    logout(request)
    return redirect('dashboard:login')

@login_required
def index(request):
    logger.info(f"User accessed index: {request.user.email} (superuser: {request.user.is_superuser})")
    context = {
        'is_superuser': request.user.is_superuser,
    }
    return render(request, 'dashboard/index.html', context)

def auth_login(request):
    try:
        strategy = load_strategy(request)
        backend = load_backend(strategy, 'google-oauth2', None)
        logger.info(f"Starting OAuth process with backend: {backend}")
        
        return do_auth(backend)
    except Exception as e:
        logger.warning(f"OAuth error: {str(e)}", exc_info=True)
        return HttpResponse(f"Authentication error: {str(e)}", status=500)

@superuser_required
def trading_status(request):
    try:
        main_account = get_main_account_info()
        if main_account:
            client = Client(main_account.api_key, main_account.api_secret)
            
            # 獲取 grid_v2 且 ACTIVE 的策略
            v2_symbols = get_active_grid_v2_symbols()
            active_strategies = Strategy.objects.filter(
                symbol__in=v2_symbols,
                status='ACTIVE'
            )
            
            # 獲取所有掛單
            open_orders = get_all_future_open_order(client)
            
            # 轉換 open_orders 格式為字典
            orders_dict = {}
            for order_info in open_orders:
                for symbol, info in order_info.items():
                    orders_dict[symbol] = info
            
            # 為所有活躍策略創建掛單資訊字典
            filtered_orders = {}
            for strategy in active_strategies:
                if strategy.symbol in orders_dict:
                    filtered_orders[strategy.symbol] = orders_dict[strategy.symbol]
                else:
                    # 如果沒有掛單，創建空的統計資訊
                    filtered_orders[strategy.symbol] = {
                        'long_orders': 0,
                        'short_orders': 0,
                        'reduce_only_orders': 0,
                        'total_orders': 0
                    }
            
            context = {
                'open_orders': filtered_orders,
                'account_info': main_account,
                'active_strategies': active_strategies,
            }
            return render(request, 'dashboard/trading_status.html', context)
        else:
            logger.error("無法獲取主帳戶資訊")
            return render(request, 'dashboard/error.html', {'error': '無法獲取主帳戶資訊'})
            
    except Exception as e:
        logger.error(f"查看交易狀態時發生錯誤: {str(e)}")
        return render(request, 'dashboard/error.html', {'error': str(e)})
    
@require_POST
@superuser_required
def execute_grid_v2_view(request, symbol):
    try:
        main_account = get_main_account_info()
        if main_account:
            client = Client(main_account.api_key, main_account.api_secret)
            
            # 從 strategy model 中取得對應的 strategy
            strategy = get_strategy_by_symbol(symbol)
            if not strategy:
                logger.error(f"找不到 {symbol} 對應的策略")
                return JsonResponse({'success': False, 'error': f'找不到 {symbol} 對應的策略'})
            
            # 執行 grid_v2_lab_2，並設置 is_reset=True
            grid_v2_lab_2(
                client=client, 
                passphrase=strategy.passphrase, 
                symbol=symbol,
                is_reset=True,
                use_lock=False
            )
            return JsonResponse({'success': True})
        else:
            logger.error("無法獲取主帳戶資訊")
            return JsonResponse({'success': False, 'error': '無法獲取主帳戶資訊'})
            
    except Exception as e:
        logger.error(f"執行 Grid V2 時發生錯誤: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})    
