variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-south-1"
}

variable "name" {
  description = "Name prefix for every resource."
  type        = string
  default     = "link-shortener"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,32}$", var.name))
    error_message = "name must be 3-32 characters from a-z, 0-9 and hyphen."
  }
}

variable "create_token" {
  description = "Bearer token callers must send to create links. Pass it with TF_VAR_create_token, never commit it."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.create_token) >= 24
    error_message = "create_token must be at least 24 characters. Generate one with: openssl rand -hex 24"
  }
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps Lambda and access logs."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.log_retention_days)
    error_message = "log_retention_days must be a value CloudWatch Logs supports."
  }
}

variable "throttle_rate_limit" {
  description = "Steady-state requests per second allowed through the API."
  type        = number
  default     = 10
}

variable "throttle_burst_limit" {
  description = "Maximum burst of requests allowed through the API."
  type        = number
  default     = 20
}

variable "table_deletion_protection" {
  description = "Block accidental deletion of the links table. Set to false before running terraform destroy."
  type        = bool
  default     = true
}
