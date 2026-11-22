output "api_url" {
  description = "Base URL of the API."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "table_name" {
  description = "Name of the DynamoDB table holding the links."
  value       = aws_dynamodb_table.links.name
}

output "lambda_function_name" {
  description = "Name of the Lambda function."
  value       = aws_lambda_function.api.function_name
}
