# Hardware used for development
- CPU: Intel Core i3-10100 @ 3.60 GHz, 4 cores / 8 threads
- RAM: 8 GB (8,123 MB)
- OS: Windows (native), Java 17 (Temurin), Python 3.11.9, PySpark 3.5.x
- SF=1 benchmark: to be run on a free cloud notebook (hardware recorded at that time)

## SF 0.25 run (laptop)

- RAM 7.9 GB, Intel Core i3-10100 (4 cores, 8 threads), Spark local mode, generator `--driver-memory 4g`.
- Generate: 138 s. Pipeline (bronze to serve): 710 s in total, no failed stage. Per-stage times: `scaling_sf025_pipeline.log`.
- Note: this machine has less RAM than the 16 GB laptop in the brief, so the SF 1 benchmark is run on a free notebook (hardware recorded at that time).
