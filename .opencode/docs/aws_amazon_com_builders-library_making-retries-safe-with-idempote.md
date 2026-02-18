# references-details-empty

> Source: https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/
> Cached: 2026-02-17T19:16:05.749Z

---

The Amazon Builders' Library

                
                 

                  - [Overview](/builders-library/)

                  - [Authors](/builders-library/authors/)

                  - [FAQs](/builders-library/faqs/)

                 

                
               
              
             
            
           
          
         
        
       
      
      
       
        
         
         
          
           
           
            
             
              
               
                
                 
                  

                   - [AWS](/)›

                   - [Amazon Builders' Library](/builders-library/)›

                   - Making retries safe with idempotent APIs

                  

                 
                
                
                 
                  Architecture | LEVEL 300
                  # Making retries safe with idempotent APIs

                  
                   
                    [Send me updates](https://pages.awscloud.com/amazon-builders-library.html)
                   
                  
                 
                
               
              
             
            
           
         
        
       
      
     
     
      
       
        
         
         
          
           
           
            
             
              [Introduction](#ams#what-isc1#pattern-data) [Retrying and side effects](#ams#what-isc2#pattern-data) [Reducing client complexity](#ams#what-isc3#pattern-data) [Retries and semantic equivalence](#ams#what-isc4#pattern-data) [Late arriving requests](#ams#what-isc5#pattern-data) [Same client request ID, different intent](#ams#what-isc6#pattern-data) [Conclusion](#ams#what-isc7#pattern-data)
             
            
           
           
            
             
              
               ## Introduction

               
                [By Malcolm Featonby | ](/builders-library/authors/malcolm-featonby/)[PDF](https://d1.awsstatic.com/builderslibrary/pdfs/making-retries-safe-with-idempotent-apis-malcolm-featonby.pdf)

                At Amazon, we often see patterns in our services in which a complex operation is decomposed into a controlling process making calls to a number of smaller services, each responsible for one part of the overall workflow. For example, consider the launch of an Amazon Elastic Compute Cloud (EC2) instance. “Under the hood” this involves calls to services responsible for making placement decisions, creating Amazon Elastic Block Store (EBS) volumes, creating elastic network Interfaces, and provisioning a virtual machine (VM). On occasion, one or more of these service calls might fail. To deliver a good customer experience we want to drive this workflow to success. To achieve this, we look to the controlling process to shepherd all decomposed services into a known good state.

                We’ve found that in many cases the simplest solution is the best solution. In the scenario I just described, it would be best to simply retry these calls until they succeed. Interestingly, as Marc Brooker explains in the article [Timeouts, retries, and backoff with jitter](/builders-library/timeouts-retries-and-backoff-with-jitter/), a surprisingly large number of transient or random faults can be overcome by simply retrying the call. As simple as it seems, this pattern has proven so effective that we have baked [default retry behavior](https://docs.aws.amazon.com/AWSJavaSDK/latest/javadoc/com/amazonaws/retry/PredefinedRetryPolicies.SDKDefaultRetryCondition.html) into some of our AWS SDK implementations. These implementations automatically retry requests that fail as a result of network IO issues, server-side fault, or service rate limiting. Being able to simply retry requests reduces the number of edge cases that need to be dealt with by the client. As a result, it reduces the amount of undifferentiated boilerplate code needed in calling services. Undifferentiated boilerplate code in this context refers to the code needed to wrap service calls to remote services to handle various fault scenarios that might arise.

                However, retrying a service call as mitigation for a transient fault is based on a simplifying assumption that an operation can be retried without any side effects. Put another way, we want to make sure that the result of the call happens only once, even if we need to make that call multiple times as part of our retry loop. Going back to our earlier example, it would be undesirable for the EC2 instance launch workflow to retry a failed call to create an EBS volume and end up with two EBS volumes. In this article, we discuss how AWS leverages idempotent API operations to mitigate some of the potential undesirable side effects of retrying in order to deliver a more robust service while still leveraging the benefits of retries to simplifying client-side code.

               
              
             
            
            
             
              
               ## Retrying and side effects

               
                ### Retrying and the potential for undesirable side effects

                To dive into this more deeply let’s consider a hypothetical scenario where a customer is using the Amazon EC2 RunInstances API operation. In our scenario, our customer wants to run a singleton workload, which is a workload that requires “at most one” EC2 instance running at any time. To achieve this, our customer’s provisioning process asks Amazon EC2 to launch this new workload. However, for some reason, perhaps due to a network timeout, the provisioning process receives no response.

                
                This leaves the provisioning process with a dilemma. It’s not clear whether the singleton workload is running or not. Simply retrying the request could result in multiple workloads, which could have dire consequences. To overcome this dilemma the provisioning process has to perform a reconciliation to determine whether this workload is running or not. This is a lot of heavy lifting to compensate for an edge case that might happen relatively infrequently. In addition, even in the case of a reconciliation workflow, there might still be some uncertainty. What if the resource is there but created by another provisioning process? In the simple case that might be fine, but in more complex scenarios it might be important to know whether the resource was created by this process or another process.

               
              
             
            
            
             
              
               ## Reducing client complexity

               
                ### Reducing client complexity with idempotent API design

                To allow callers to retry these kinds of operations we need to make them idempotent. An idempotent operation is one where a request can be retransmitted or retried with no additional side effects, a property that is very beneficial in distributed systems.

                We can significantly simplify client code by delivering a contract that allows the client to make a simplifying assumption that any error that isn’t a validation error can be overcome by retrying the request until it succeeds. However, this introduces some additional complexity to service implementation. In a distributed system with many clients making many calls and with many requests in flight, the challenge is how do we identify that a request is a repeat of some previous request?

                Many approaches could be used to infer whether a request is a duplicate of an earlier request. For example, it might be possible to derive a synthetic token based on the parameters in the request. You could derive a hash of the parameters present and assume that any request from the same caller with identical parameters is a duplicate. On the surface, this seems to simplify both the customer experience and the service implementation. Any request that looks exactly like a previous request is considered a duplicate. However, we have found that this approach doesn’t work in all cases. For example, it might be reasonable to assume that two exact duplicate requests from the same caller to create an Amazon DynamoDB table received very close together in time are duplicates of the same request. However, if those requests were for launching an Amazon EC2 instance, then our assumption might not hold. It’s possible that the caller actually wants two identical EC2 instances.

                At Amazon, our preferred approach is to incorporate a unique caller-provided client request identifier into our API contract. Requests from the same caller with the same client request identifier can be considered duplicate requests and can be dealt with accordingly. By allowing customers to clearly express intent through API semantics we want to reduce the potential for unexpected outcomes for the customer. A unique caller-provided client request identifier for idempotent operations meets this need. It also has the benefit of making that intent readily auditable because the unique identifier is present in logs like AWS CloudTrail. Furthermore, by labeling the created resource with the unique client request identifier, customers are able to identify resources created by any given request. A concrete example of this can be seen in the Amazon EC2 DescribeInstances response, which shows the unique identifier used to create the EC2 instance. (In the Amazon EC2 API the unique client request identifier is called the ClientToken).

                The following diagram shows a sample request/response flow that uses a unique client request identifier in an idempotent retry scenario:

                

                In this example, a customer requests the creation of a resource that presents a unique client request identifier. On receiving the request, the service first checks to see if it has seen this identifier before. If it has not, it starts to process the request. It creates an idempotent “session” for this request keyed off the customer identifier and their unique client request identifier. If a subsequent request is received from the same customer with the same unique client request identifier then the service knows it has already seen this request and can take appropriate action. An important consideration is that the process that combines recording the idempotent token and all mutating operations related to servicing the request must meet the properties for an atomic, consistent, isolated, and durable (ACID) operation. An ACID server-side operation needs to be an “all or nothing” or atomic process. This ensures that we avoid situations where we could potentially record the idempotent token and fail to create some resources or, alternatively, create the resources and fail to record the idempotent token.

                The previous diagram shows the preparation of a semantically equivalent response in cases where the request has already been seen. It could be argued that this is not required to meet the letter of the law for an operation to be idempotent. Consider the case where a hypothetical CreateResource operation is called with the unique request identifier123. If the first request is received and processed but the response never makes it back to the caller then the caller will retry the request with identifier 123. However, the resource might now have been created as part of the initial request. One possible response to this request is to return a ResourceAlreadyExists return code. This meets the basic tenets for idempotency because there is no side effect for retrying the call. However, this leads to uncertainty from the perspective of the caller because it’s not clear whether the resource was created as a result of this request or the resource was created as the result of an earlier request. It also makes introducing retry as the default behavior a little more challenging. This is because, although the request had no side effects, the subsequent retry and resultant return code will likely change the flow of execution for the caller. Now the caller needs to deal with the resource already existing even in cases where (from their perspective) it did not exist before they made the call. In this scenario, although there is no side effect from the service perspective, returning ResourceAlreadyExists has a side effect from the client’s perspective.

               
              
             
            
            
             
              
               ## Retries and semantic equivalence

               
                ### Semantic equivalence and support for default retry strategies

                An alternative is to deliver a semantically equivalent response in every case for the same unique request identifier for some interval. This means that any subsequent response to a retry request from the same caller with the same unique client request identifier will have the same meaning as the first response returned for the first successful request. This approach has some really useful properties—especially where we want to improve the customer experience by safely and simply retrying operations that experience server-side faults, just as we do with the AWS SDK through default retry policies.

                We can see an example of idempotency with semantically equivalent responses and automated retry logic in action when we use the Amazon EC2 RunInstances API operation and the AWS Command Line Interface (CLI). Note that the AWS CLI (like the AWS SDK) [supports a default retry policy](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-retries.html), which we are using here. In this example, we launch an EC2 instance using the following AWS CLI command:

                `$ aws ec2 run-instances --image-id ami-04fcd96153cb57194 --instance-type t2.micro  `

                `{ `

                `    "Instances": [ `

                ``     { 

                        "Monitoring": { 

                            "State": "disabled"

                        },

                        "StateReason": {

                             "Message": "pending",

                             "Code": "pending"

                        },

                        "State": {

                              "Code": 0

                              "Name": "pending"

                         },

                        "InstanceId": "i-xxxxxxxxxxxxxxxxx",

                        "ImageId": "ami-04fcd96153cb57194",         

                        …

                        "ClientToken": "eb3c3141-a229-4ca0-b005-eb922e2cabdc",

                        …                                                                                                                                                                                                                                       "ClientToken": "eb3c3141-a229-4ca0-b005-

... [Content truncated]