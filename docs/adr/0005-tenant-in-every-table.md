# ADR-0005. tenant_id in every table; the law is a shared corpus

Decision. Every row has a tenant_id. Fragments of the law have tenant_id = 'law' and are available to everyone; every search query filters by `tenant_id IN (<company>, 'law')`. The cache keys and traces contain company_id.
