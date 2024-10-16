build:
	docker build --tag ldn:local .

run-one:
	docker run --rm \
		-e AWS_BATCH_JOB_ARRAY_INDEX=0 \
		-p 8787:8787 \
		--rm ldn:local \
			ldn-processor \
				--tile=47,238

run-env:
	docker run \
		-e AWS_BATCH_JOB_ARRAY_INDEX=0 \
		-p 8787:8787 \
		--rm ldn:local \
			ldn-processor \
				--year=2023