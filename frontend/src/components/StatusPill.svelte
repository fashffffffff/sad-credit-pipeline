<script lang="ts">
  import type { AppStatus } from '../lib/types'
  import { CLIENT_STATUS_LABEL, STATUS_LABEL, STATUS_TONE } from '../lib/format'
  // client — тексты без жаргона («Смотрит специалист»), по умолчанию — для сотрудников
  let { status, client = false }: { status: AppStatus; client?: boolean } = $props()
</script>

<span class="pill {STATUS_TONE[status]}">{(client ? CLIENT_STATUS_LABEL : STATUS_LABEL)[status]}</span>
