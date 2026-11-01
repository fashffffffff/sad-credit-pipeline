<script lang="ts">
  import type { AppStatus } from '../lib/types'
  import { STATUS_LABEL } from '../lib/format'
  let { status }: { status: AppStatus } = $props()
</script>

<span class="pill {status}">{STATUS_LABEL[status]}</span>
