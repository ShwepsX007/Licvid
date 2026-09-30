# Whale Alert: стоимость, ограничения и фактическое покрытие

Alchemy Free даёт 30 млн CU/месяц на всё приложение, а не на каждую сеть.
Локальный скринер резервирует 10 млн CU/месяц по умолчанию; лимит можно менять
в `/admin` (1 000–20 млн CU). Это **не** остаток из Alchemy Dashboard и не
гарантия, что столько CU осталось у аккаунта: другие части приложения и другие
ключи того же аккаунта тоже расходуют общий тариф. Несколько ключей помогают
переключаться при ограничениях, но не умножают локальный общий бюджет.

## Режимы и локальный учёт

- **`realtime` (по умолчанию):** live-подписки на известные CEX-адреса плюс
  догоняющее сканирование разрешённых EVM-переводов; интервал истории по
  умолчанию 5 минут.
- **`economy`:** live-подписки на известные CEX-адреса остаются включены, а
  фоновое EVM-сканирование пропускает переводы, не относящиеся к известным
  CEX-кошелькам; интервал истории по умолчанию 15 минут.
- Исторический интервал настраивается отдельно от live-подписок: 5, 10, 15,
  30 или 60 минут. Первый запуск сохраняет текущую высоту без старого replay;
  курсоры продолжают догонять ограниченными диапазонами после простоя.
- Локальный CU ledger считает HTTP JSON-RPC методы по фиксированной цене:

  | Метод | Локальная цена |
  | --- | ---: |
  | `eth_blockNumber` | 10 CU |
  | `eth_getLogs` | 60 CU |
  | `alchemy_getAssetTransfers` | 120 CU |
  | Solana `getSignaturesForAddress` | 40 CU |
  | Solana `getTransaction` | 40 CU |

  Стоимость WebSocket-соединений/подписок, лимиты TronGrid и общий расход
  аккаунта в этот локальный ledger не входят. Сверяйте фактический billing и
  rate limits в консолях провайдеров. Стоимость методов может измениться.
- Глобальный обход каждого EVM-блока не включён: одна только Arbitrum при
  номинальных 250 мс и 20 CU за блок стоила бы около **207 360 000 CU за 30
  дней**, ещё без токенов, других сетей, повторов и цен.
- Если в реестре нет Solana CEX-адресов, резервный поток подписывается на
  Token Program (`logsSubscribe` с одним `mentions`) и запрашивает `getTransaction`
  только для SPL Transfer/TransferChecked. В очередь попадают USDT/USDC-переводы
  **строго выше $100 000**. Очередь ограничена 64 сигнатурами; один worker
  обрабатывает их с pace limit, а повторные signature ограниченно дедуплицируются.
  Fallback резервирует не более 10% локального месячного CU и не более 1 млн CU
  по умолчанию (`LIQSCOPE_SOLANA_FALLBACK_CU`); вызовы распределяются по месяцу.
  Размер резерва и интервал можно настроить переменными
  `LIQSCOPE_SOLANA_FALLBACK_CU` и `LIQSCOPE_SOLANA_FALLBACK_INTERVAL_SEC`.

## Поддерживаемый поток

| Сеть | Live / исторический источник | Активы и ограничения |
| --- | --- | --- |
| Ethereum | Alchemy WS (ERC-20 и подтверждённые нативные CEX-переводы); HTTP catch-up | USDT, USDC, DAI, WBTC; ETH |
| BNB Chain | Alchemy WS и `eth_getLogs` по CEX-адресам; HTTP catch-up | USDT, USDC, DAI, BTCB; нативный BNB не индексируется этим адаптером |
| Polygon | Alchemy WS (ERC-20 и подтверждённые нативные CEX-переводы); HTTP catch-up | USDT, два USDC, POL |
| Arbitrum | Alchemy WS (ERC-20 и подтверждённые нативные CEX-переводы); HTTP catch-up | USDT, USDC, DAI, WBTC, ETH |
| Base | Alchemy WS для ERC-20; HTTP catch-up | USDC, ETH через Transfers API; mined-native WS здесь не включён |
| Solana | Alchemy WS по известным CEX account/SPL token accounts; если реестр пуст — Token Program logs fallback + `getTransaction` | SOL, USDT, USDC; при fallback только USDT/USDC >$100K; CEX подписки ограничены `LIQSCOPE_SOLANA_MAX_WALLETS` (250) |
| Tron | TronGrid: подтверждённые блоки и TRC-20 Transfer events примерно раз в 3 секунды | TRX, USDT, USDC; ключ необязателен, публичный доступ ограничен квотами |
| Hyperliquid Core | Нативный публичный Hyperliquid API: `meta` + WS `trades` | Рыночные fills от $50K, не депозиты/выводы; прежняя Hyperliquid API-логика сохранена, Alchemy не используется |

Список CEX-кошельков автоматически обновляется из публичного DeFiLlama adapter config;
для ETH есть fallback-источник Etherscan labels. Администраторы могут добавлять,
редактировать и удалять записи вручную. Список может быть неполным или устареть:
биржи меняют адреса, а не все сети/кошельки индексируются одинаково.
Секретные ключи Alchemy и TronGrid хранятся зашифрованными; API возвращают
только публичные маскированные подсказки.

Это не полный индекс всех сетевых переводов или балансов. Покрытие ограничено
известными адресами, выбранными токенами, фильтром суммы и доступностью
провайдеров. Bitcoin, Bitcoin Cash, Litecoin, Sui и Dogecoin не подключены.
История скринера сохраняется в SQLite до семи дней; live-карточки и отдельная
лента Hyperliquid показывают источник события (`⚡` live или `🕒` historical).

## Ключи и диагностика

В `/admin` можно добавить до восьми Alchemy API keys и TronGrid key. Они
зашифрованы с помощью постоянного `LIQSCOPE_SECRET` в
`data/alchemy_keys.enc` и `data/trongrid_keys.enc`, исключены из Git и никогда
не возвращаются клиенту. Для локальной квоты Alchemy ведутся оценки по методу,
сети, ключу и месяцу; страница администрирования показывает фактическое
локальное потребление и оценку месячного расхода, а не число из биллинга.

Страница `/screener` закрыта для гостей; `/api/screener/*` сохраняет тот же
доступ. Alchemy/TronGrid/CEX-wallet admin API доступны только администраторам.
Статусы сетей и последние ошибки доступны в `/screener` и `/admin`.

Источники цен/методов: [план Alchemy](https://www.alchemy.com/docs/reference/pricing-plans),
[стоимость методов](https://www.alchemy.com/docs/reference/compute-unit-costs),
[Alchemy Transfers API](https://www.alchemy.com/docs/data/transfers-api/transfers-endpoints/alchemy-get-asset-transfers),
[DeFiLlama CEX metadata](https://api.llama.fi/cexs).